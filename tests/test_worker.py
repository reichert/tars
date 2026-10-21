import gc
import threading
import weakref

from whisper_ditado.worker import SerialWorker


class Resource:
    pass


def test_idle_worker_does_not_retain_completed_arguments():
    worker = SerialWorker("test-worker-release")
    resource = Resource()
    ref = weakref.ref(resource)
    future = worker.submit(lambda value: None, resource)
    del resource
    future.result(2)
    worker._queue.join()
    gc.collect()
    assert ref() is None


def test_cancelled_arguments_are_released_and_worker_keeps_running():
    worker = SerialWorker("test-worker-cancel")
    started, proceed = threading.Event(), threading.Event()

    def wait():
        started.set()
        assert proceed.wait(2)

    worker.submit(wait)
    assert started.wait(2)
    resource = Resource()
    ref = weakref.ref(resource)
    future = worker.submit(lambda value: None, resource)
    del resource
    assert future.cancel()
    proceed.set()
    worker._queue.join()
    gc.collect()
    assert ref() is None
    assert worker.submit(lambda: 42).result(2) == 42
