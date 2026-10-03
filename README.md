# Triage Queue

Gestor de cola de prioridad para el triaje de urgencias, en Python. Solo usa la biblioteca estándar.

- Nivel 1 (crítico) siempre antes que nivel 2 (urgente), y nivel 2 antes que nivel 3 (estándar).
- Dentro de un mismo nivel, estricto orden de llegada (FIFO).
- Operaciones: `enqueue`, `dequeue`, `peek`, `list_queue`, `stats`.
- Seguro entre hilos: varios workers pueden encolar y desencolar a la vez.

## Uso

Requiere Python 3.10 o superior.

```bash
python triage_queue.py          # menú interactivo
python -m unittest -v           # tests
```

Como librería:

```python
from triage_queue import Patient, TriageQueue

q = TriageQueue()
q.enqueue(Patient("Ana", 3))
q.enqueue(Patient("Luis", 1))
q.peek()        # Luis
q.list_queue()  # [Luis, Ana]
q.stats()       # {1: 1, 2: 0, 3: 1}
q.dequeue()     # Luis
```

`dequeue()` y `peek()` sobre una cola vacía lanzan `EmptyQueueError`, que es una subclase de `IndexError` y lleva un mensaje descriptivo. El menú la captura y muestra el aviso sin cerrarse.

## Estructura

| Archivo                | Contenido                                                     |
|------------------------|---------------------------------------------------------------|
| `triage_queue.py`      | `Patient` (dataclass), `TriageQueue` (heap + lock), `TriageCLI` (menú) |
| `test_triage_queue.py` | Tests de orden, FIFO, casos borde, concurrencia y CLI         |
| `DESIGN.md`            | Por qué un heap y cómo se evita el doble procesamiento concurrente |
