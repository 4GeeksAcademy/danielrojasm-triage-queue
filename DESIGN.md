# Notas de diseño

## 1. Estructura de datos interna

`TriageQueue` usa un **min-heap (`heapq`)** de tuplas `(triage_level, seq, patient)`.

- `triage_level` va primero, así que un nivel 1 siempre sale antes que un 2, y un 2 antes que un 3.
- `seq` es un contador que crece de uno en uno (`itertools.count`) y se asigna al encolar. Desempata dentro del mismo nivel y da un FIFO estricto.
  Uso `seq` en lugar de `arrived_at` porque dos pacientes pueden registrarse con el mismo timestamp (la resolución del reloj es limitada, o se cargan en lote). Además, como `seq` es único, `heapq` nunca tiene que comparar dos objetos `Patient`.
- Un diccionario `_counts` se actualiza en cada enqueue/dequeue, así que `stats()` es O(1) y no hay que recorrer la cola.

| Operación    | Coste       |
|--------------|-------------|
| `enqueue`    | O(log n)    |
| `dequeue`    | O(log n)    |
| `peek`       | O(1)        |
| `stats`      | O(1)        |
| `list_queue` | O(n log n), ordena una copia sin tocar el heap |

**Por qué no las alternativas:**

- **Una sola `deque`:** solo es FIFO. Para que un nivel 1 se salte la cola hay que buscar su posición e insertar en medio, lo que cuesta O(n) por inserción. Justo lo que pide evitar el enunciado.
- **Lista ordenada (`bisect.insort`):** encontrar la posición es O(log n), pero insertar desplaza los elementos y cuesta O(n). Además, si se saca por el principio, `pop(0)` también es O(n).
- **Tres `deque` separadas, una por nivel:** es una alternativa válida y hasta más rápida (todo O(1)) mientras los niveles sean exactamente tres y fijos. Elegí el heap porque la clave de prioridad es una tupla genérica. Si mañana el hospital pasa a la escala Manchester (5 niveles), o se añade *aging* (subir la prioridad de quien lleva mucho esperando) o una puntuación compuesta, basta con cambiar la clave. No hay que añadir más colas ni lógica de "busca la primera cola que no esté vacía". Con n del orden de decenas o cientos de pacientes, la diferencia entre O(1) y O(log n) es despreciable.

## 2. Concurrencia: un worker desencola mientras otro encola un crítico

Todo el estado mutable (`_heap`, `_counter`, `_counts`) está protegido por **un único `threading.Lock`**. Cada operación es una sección crítica completa:

- **`enqueue`:** adquirir el lock → asignar `seq` → `heappush` → incrementar `_counts` → liberar.
  El `seq` se asigna *dentro* del lock. Así el orden FIFO es el orden real de entrada en la cola y no el orden en que cada hilo creó su objeto.
- **`dequeue`:** adquirir el lock → comprobar que no está vacía → `heappop` → decrementar `_counts` → liberar → devolver el paciente.
  "Leer el siguiente" y "retirarlo" son **un solo paso atómico**. Nunca hay un *peek-y-luego-pop* en dos pasos, que permitiría a dos workers ver al mismo paciente y atenderlo dos veces.

Con esto, si un worker A desencola a la vez que un worker B encola un crítico, el lock los serializa y solo hay dos resultados posibles. Ambos son correctos:

1. **A entra primero:** se lleva al que era el siguiente en ese instante. El crítico de B queda el primero para la próxima llamada.
2. **B entra primero:** el crítico ya está en la cima del heap y A se lo lleva.

En ningún caso un paciente se pierde ni se atiende dos veces, y `stats()` nunca ve un estado intermedio porque el contador se modifica dentro de la misma sección crítica que el heap. `peek()` y `list_queue()` también toman el lock: `list_queue` copia el heap bajo el lock y lo ordena fuera de él, para no bloquear a los demás workers mientras ordena.

`peek()` es solo informativo: su resultado puede quedar obsoleto en cuanto se libera el lock. Por eso un worker **nunca** debe hacer `peek()` y después decidir atender a ese paciente. Siempre debe usar `dequeue()`.

**En un sistema distribuido** (varios procesos o máquinas) el lock en memoria no sirve. La misma idea se traslada a una operación atómica de "reclamar" en el almacén compartido: `SELECT ... FOR UPDATE SKIP LOCKED` en PostgreSQL, `ZPOPMIN` en Redis, o un *lease* o *visibility timeout* en una cola de mensajes. Primero se marca el paciente como reclamado por un worker concreto, de forma atómica, y solo después se procesa. Si el worker cae, el *lease* expira y el paciente vuelve a la cola en lugar de perderse.

El test `test_concurrent_workers_never_lose_or_duplicate_patients` lanza 4 productores y 4 consumidores sobre 2000 pacientes. Comprueba que cada paciente se atiende exactamente una vez y que `stats()` termina a cero.
