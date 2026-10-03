"""Triage Queue — gestor de cola de prioridad para urgencias.

Punto de entrada: `python triage_queue.py`

Estructura del módulo:
    - Patient:     modelo de datos de un paciente (inmutable).
    - TriageQueue: cola de prioridad (heapq) segura entre hilos.
    - TriageCLI:   menú de texto en bucle que usa TriageQueue.

Las notas de diseño completas están en DESIGN.md.
"""

from __future__ import annotations

import heapq
import itertools
import threading
from dataclasses import dataclass, field
from datetime import datetime
from typing import Callable

TRIAGE_LEVELS = {1: "crítico", 2: "urgente", 3: "estándar"}


class EmptyQueueError(IndexError):
    """Se lanza al intentar leer o extraer de una cola sin pacientes."""


@dataclass(frozen=True)
class Patient:
    """Paciente registrado en triaje.

    `arrived_at` se rellena automáticamente con la hora actual si no se indica.
    """

    name: str
    triage_level: int
    arrived_at: datetime = field(default_factory=datetime.now)

    def __post_init__(self) -> None:
        if not isinstance(self.name, str) or not self.name.strip():
            raise ValueError("El nombre del paciente no puede estar vacío.")
        # bool es subclase de int: lo excluimos explícitamente.
        if isinstance(self.triage_level, bool) or not isinstance(self.triage_level, int):
            raise TypeError("El nivel de triaje debe ser un entero.")
        if self.triage_level not in TRIAGE_LEVELS:
            raise ValueError(
                f"Nivel de triaje inválido: {self.triage_level}. "
                f"Valores permitidos: {sorted(TRIAGE_LEVELS)}."
            )
        object.__setattr__(self, "name", self.name.strip())

    def __str__(self) -> str:
        label = TRIAGE_LEVELS[self.triage_level]
        return (
            f"{self.name} (nivel {self.triage_level} - {label}, "
            f"llegada {self.arrived_at:%H:%M:%S})"
        )


class TriageQueue:
    """Cola de prioridad de triaje.

    Internamente es un min-heap de tuplas `(triage_level, seq, patient)`:
      - `triage_level` ordena primero por gravedad (1 antes que 2 antes que 3).
      - `seq` es un contador monotónico asignado al encolar; desempata dentro
        del mismo nivel en estricto orden de llegada (FIFO). Se usa en vez de
        `arrived_at` porque dos llegadas pueden compartir timestamp, y porque
        garantiza que nunca se llegue a comparar objetos `Patient`.

    Complejidad: enqueue/dequeue O(log n), peek/stats O(1), list_queue O(n log n).

    Todas las operaciones se ejecutan bajo un mismo lock, de modo que varios
    workers pueden encolar y desencolar a la vez sin perder ni duplicar
    pacientes (ver DESIGN.md).
    """

    def __init__(self) -> None:
        self._heap: list[tuple[int, int, Patient]] = []
        self._counter = itertools.count()
        self._counts = {level: 0 for level in TRIAGE_LEVELS}
        self._lock = threading.Lock()

    def enqueue(self, patient: Patient) -> None:
        """Añade un paciente respetando nivel de triaje y orden de llegada."""
        if not isinstance(patient, Patient):
            raise TypeError("Solo se pueden encolar objetos Patient.")
        with self._lock:
            # El número de secuencia se asigna dentro del lock: el orden FIFO
            # es el orden en que el paciente entra realmente en la cola.
            entry = (patient.triage_level, next(self._counter), patient)
            heapq.heappush(self._heap, entry)
            self._counts[patient.triage_level] += 1

    def dequeue(self) -> Patient:
        """Extrae y devuelve el siguiente paciente a atender.

        Raises:
            EmptyQueueError: si no hay pacientes en espera.
        """
        with self._lock:
            if not self._heap:
                raise EmptyQueueError("No hay pacientes en espera: la cola está vacía.")
            _, _, patient = heapq.heappop(self._heap)
            self._counts[patient.triage_level] -= 1
            return patient

    def peek(self) -> Patient:
        """Devuelve el siguiente paciente sin extraerlo.

        Raises:
            EmptyQueueError: si no hay pacientes en espera.
        """
        with self._lock:
            if not self._heap:
                raise EmptyQueueError("No hay pacientes en espera: la cola está vacía.")
            return self._heap[0][2]

    def list_queue(self) -> list[Patient]:
        """Devuelve todos los pacientes en espera, en orden de atención."""
        with self._lock:
            snapshot = list(self._heap)
        return [patient for _, _, patient in sorted(snapshot)]

    def stats(self) -> dict[int, int]:
        """Devuelve {nivel_de_triaje: pacientes_en_espera} para los tres niveles."""
        with self._lock:
            return dict(self._counts)

    def __len__(self) -> int:
        with self._lock:
            return len(self._heap)

    def is_empty(self) -> bool:
        return len(self) == 0


class TriageCLI:
    """Menú de texto en bucle sobre una TriageQueue.

    `input_fn` y `output_fn` son inyectables para poder probar el menú sin
    teclado (por defecto, `input` y `print`).
    """

    MENU = (
        "\n=== Triage Queue ===\n"
        "1. Añadir paciente\n"
        "2. Llamar al siguiente paciente\n"
        "3. Ver siguiente paciente (peek)\n"
        "4. Ver cola actual\n"
        "5. Ver estadísticas\n"
        "6. Salir"
    )

    def __init__(
        self,
        queue: TriageQueue | None = None,
        input_fn: Callable[[str], str] = input,
        output_fn: Callable[[str], None] = print,
    ) -> None:
        self.queue = queue if queue is not None else TriageQueue()
        self._input = input_fn
        self._print = output_fn
        self._actions: dict[str, Callable[[], None]] = {
            "1": self.add_patient,
            "2": self.call_next,
            "3": self.show_next,
            "4": self.show_queue,
            "5": self.show_stats,
        }

    def run(self) -> None:
        """Bucle principal. Termina con la opción 6, Ctrl+C o fin de entrada."""
        while True:
            self._print(self.MENU)
            try:
                choice = self._input("Elige una opción: ").strip()
                if choice == "6":
                    self._print("Hasta luego.")
                    return
                action = self._actions.get(choice)
                if action is None:
                    self._print("Opción no válida. Introduce un número del 1 al 6.")
                    continue
                action()
            except (EOFError, KeyboardInterrupt):
                self._print("\nSaliendo.")
                return

    # --- Acciones del menú -------------------------------------------------

    def add_patient(self) -> None:
        name = self._ask_name()
        level = self._ask_level()
        patient = Patient(name=name, triage_level=level)
        self.queue.enqueue(patient)
        self._print(f"Paciente añadido: {patient}")

    def call_next(self) -> None:
        try:
            patient = self.queue.dequeue()
        except EmptyQueueError as exc:
            self._print(str(exc))
            return
        self._print(f"Siguiente paciente a atender: {patient}")

    def show_next(self) -> None:
        try:
            patient = self.queue.peek()
        except EmptyQueueError as exc:
            self._print(str(exc))
            return
        self._print(f"Próximo en ser llamado: {patient}")

    def show_queue(self) -> None:
        patients = self.queue.list_queue()
        if not patients:
            self._print("La cola está vacía.")
            return
        self._print(f"Pacientes en espera ({len(patients)}):")
        for position, patient in enumerate(patients, start=1):
            self._print(f"  {position}. {patient}")

    def show_stats(self) -> None:
        stats = self.queue.stats()
        self._print("Pacientes en espera por nivel:")
        for level, count in stats.items():
            self._print(f"  Nivel {level} ({TRIAGE_LEVELS[level]}): {count}")
        self._print(f"  Total: {sum(stats.values())}")

    # --- Lectura validada de entrada ---------------------------------------

    def _ask_name(self) -> str:
        while True:
            name = self._input("Nombre del paciente: ").strip()
            if name:
                return name
            self._print("El nombre no puede estar vacío.")

    def _ask_level(self) -> int:
        options = ", ".join(f"{k} = {v}" for k, v in TRIAGE_LEVELS.items())
        while True:
            raw = self._input(f"Nivel de triaje ({options}): ").strip()
            if raw in {str(level) for level in TRIAGE_LEVELS}:
                return int(raw)
            self._print("Nivel inválido. Introduce 1, 2 o 3.")


def main() -> None:
    TriageCLI().run()


if __name__ == "__main__":
    main()
