"""Tests de TriageQueue y TriageCLI. Ejecutar con: python -m unittest -v"""

import threading
import unittest
from datetime import datetime

from triage_queue import EmptyQueueError, Patient, TriageCLI, TriageQueue


def names(patients):
    return [p.name for p in patients]


class PatientTests(unittest.TestCase):
    def test_arrived_at_defaults_to_now(self):
        before = datetime.now()
        patient = Patient("Ana", 2)
        self.assertLessEqual(before, patient.arrived_at)

    def test_rejects_invalid_level(self):
        for level in (0, 4, -1):
            with self.assertRaises(ValueError):
                Patient("Ana", level)
        for level in ("1", 1.0, True):
            with self.assertRaises(TypeError):
                Patient("Ana", level)

    def test_rejects_empty_name(self):
        with self.assertRaises(ValueError):
            Patient("   ", 1)


class TriageQueueTests(unittest.TestCase):
    def setUp(self):
        self.queue = TriageQueue()

    def test_levels_are_served_in_priority_order(self):
        self.queue.enqueue(Patient("Estándar", 3))
        self.queue.enqueue(Patient("Urgente", 2))
        self.queue.enqueue(Patient("Crítico", 1))
        self.assertEqual(
            names(self.queue.dequeue() for _ in range(3)),
            ["Crítico", "Urgente", "Estándar"],
        )

    def test_critical_arrival_jumps_ahead_of_waiting_patients(self):
        for name, level in [("A", 2), ("B", 3), ("C", 2), ("D", 3)]:
            self.queue.enqueue(Patient(name, level))
        self.queue.dequeue()  # se está procesando la cola: sale A
        self.queue.enqueue(Patient("Crítico", 1))
        self.assertEqual(self.queue.peek().name, "Crítico")
        self.assertEqual(names(self.queue.list_queue()), ["Crítico", "C", "B", "D"])

    def test_same_level_is_strict_fifo(self):
        for i in range(50):
            self.queue.enqueue(Patient(f"P{i}", 2))
        self.assertEqual(
            names(self.queue.dequeue() for _ in range(50)),
            [f"P{i}" for i in range(50)],
        )

    def test_fifo_with_identical_timestamps(self):
        ts = datetime(2026, 1, 1, 12, 0, 0)
        self.queue.enqueue(Patient("Primero", 1, ts))
        self.queue.enqueue(Patient("Segundo", 1, ts))
        self.assertEqual(names(self.queue.list_queue()), ["Primero", "Segundo"])

    def test_peek_does_not_remove(self):
        self.queue.enqueue(Patient("Ana", 2))
        self.assertEqual(self.queue.peek().name, "Ana")
        self.assertEqual(len(self.queue), 1)

    def test_list_queue_does_not_mutate(self):
        for name, level in [("A", 3), ("B", 1), ("C", 2)]:
            self.queue.enqueue(Patient(name, level))
        self.assertEqual(names(self.queue.list_queue()), ["B", "C", "A"])
        self.assertEqual(names(self.queue.list_queue()), ["B", "C", "A"])
        self.assertEqual(len(self.queue), 3)

    def test_empty_queue_raises_descriptive_error(self):
        with self.assertRaisesRegex(EmptyQueueError, "vacía"):
            self.queue.dequeue()
        with self.assertRaisesRegex(EmptyQueueError, "vacía"):
            self.queue.peek()
        self.assertEqual(self.queue.list_queue(), [])

    def test_stats_tracks_enqueue_and_dequeue(self):
        self.assertEqual(self.queue.stats(), {1: 0, 2: 0, 3: 0})
        for level in (1, 2, 2, 3, 3, 3):
            self.queue.enqueue(Patient("X", level))
        self.assertEqual(self.queue.stats(), {1: 1, 2: 2, 3: 3})
        self.queue.dequeue()
        self.assertEqual(self.queue.stats(), {1: 0, 2: 2, 3: 3})

    def test_stats_returns_a_copy(self):
        self.queue.stats()[1] = 99
        self.assertEqual(self.queue.stats()[1], 0)

    def test_concurrent_workers_never_lose_or_duplicate_patients(self):
        total = 2000
        served, served_lock = [], threading.Lock()

        def producer(offset):
            for i in range(total // 4):
                self.queue.enqueue(Patient(f"P{offset + i}", (i % 3) + 1))

        def consumer():
            while True:
                try:
                    patient = self.queue.dequeue()
                except EmptyQueueError:
                    if done.is_set():
                        return
                    continue
                with served_lock:
                    served.append(patient.name)

        done = threading.Event()
        producers = [threading.Thread(target=producer, args=(k * total,)) for k in range(4)]
        consumers = [threading.Thread(target=consumer) for _ in range(4)]
        for t in producers + consumers:
            t.start()
        for t in producers:
            t.join()
        done.set()
        for t in consumers:
            t.join()

        self.assertEqual(len(served), total)
        self.assertEqual(len(set(served)), total)
        self.assertEqual(self.queue.stats(), {1: 0, 2: 0, 3: 0})


class TriageCLITests(unittest.TestCase):
    def run_cli(self, inputs):
        feed = iter(inputs)
        output = []

        def fake_input(prompt):
            try:
                return next(feed)
            except StopIteration:
                raise EOFError

        cli = TriageCLI(input_fn=fake_input, output_fn=output.append)
        cli.run()
        return cli, "\n".join(output)

    def test_full_flow(self):
        cli, out = self.run_cli(["1", "Ana", "3", "1", "Luis", "1", "4", "5", "2", "6"])
        self.assertIn("Siguiente paciente a atender: Luis", out)
        self.assertIn("Nivel 1 (crítico): 1", out)
        self.assertEqual(names(cli.queue.list_queue()), ["Ana"])
        self.assertIn("Hasta luego.", out)

    def test_invalid_inputs_do_not_crash(self):
        cli, out = self.run_cli(["x", "", "9", "1", "", "Eva", "abc", "0", "4", "2.5", "2", "6"])
        self.assertIn("Opción no válida", out)
        self.assertIn("El nombre no puede estar vacío.", out)
        self.assertIn("Nivel inválido", out)
        self.assertEqual(cli.queue.peek().triage_level, 2)

    def test_empty_queue_operations_are_graceful(self):
        _, out = self.run_cli(["2", "3", "4", "6"])
        self.assertEqual(out.count("la cola está vacía"), 2)
        self.assertIn("La cola está vacía.", out)

    def test_end_of_input_exits_cleanly(self):
        _, out = self.run_cli(["1", "Ana"])  # EOF a mitad de alta
        self.assertIn("Saliendo.", out)


if __name__ == "__main__":
    unittest.main()
