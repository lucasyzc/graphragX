from abc import ABC, abstractmethod

from app.ir.models import FileIR


class BaseExtractor(ABC):
    @abstractmethod
    def supports(self, file_path: str) -> bool:
        raise NotImplementedError

    @abstractmethod
    def extract(self, project_id: str, file_path: str, content: str) -> FileIR:
        raise NotImplementedError
