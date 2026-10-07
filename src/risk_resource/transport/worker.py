from risk_resource.adapters.worker import JobWorker


def run_worker(repository, owner: str = "worker-1"):
    worker = JobWorker(repository, owner)
    while worker.run_once():
        pass
