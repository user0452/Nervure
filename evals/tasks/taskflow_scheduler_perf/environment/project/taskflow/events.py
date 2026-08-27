from dataclasses import dataclass

@dataclass(frozen=True)
class Event:
    kind: str
    task_id: str
    detail: str = ''

class EventLog:

    def __init__(self):
        self.events = []

    def add(self, kind, task_id, detail=''):
        self.events.append(Event(kind, task_id, detail))
