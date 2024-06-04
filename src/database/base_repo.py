from abc import ABC, abstractmethod

class BaseRepository(ABC):
    @abstractmethod
    def connect(self):
        pass
    
    @abstractmethod
    def close(self):
        pass
    
    
    @abstractmethod
    def find_all_users(self):
        pass

    @abstractmethod
    def find_user_by_id(self, id):
        pass
        
    @abstractmethod
    def get_password(self):
        pass