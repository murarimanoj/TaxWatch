from .domain import RunSummary, Source
from .pipeline import IngestionPipeline


class SourceJob:
    """Scheduler-friendly callable: one instance represents one regulator job."""

    def __init__(self, source: Source, pipeline: IngestionPipeline) -> None:
        self.source = source
        self.pipeline = pipeline

    def __call__(self, limit: int) -> RunSummary:
        return self.pipeline.run(limit)


class RbiJob(SourceJob):
    pass


class CbdtJob(SourceJob):
    pass


class SebiJob(SourceJob):
    pass

