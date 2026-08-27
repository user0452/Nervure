from dataclasses import dataclass

@dataclass(frozen=True)
class ResourceRequest:
    cpu: int = 1
    memory_mb: int = 128
    labels: frozenset[str] = frozenset()

@dataclass
class ResourcePool:
    cpu: int
    memory_mb: int
    labels: set[str]

    def can_fit(self, request):
        return request.cpu <= self.cpu and request.memory_mb <= self.memory_mb and (set(request.labels) <= self.labels)

    def allocate(self, request):
        if not self.can_fit(request):
            return False
        self.cpu -= request.cpu
        self.memory_mb -= request.memory_mb
        return True

    def release(self, request):
        self.cpu += request.cpu
        self.memory_mb += request.memory_mb
