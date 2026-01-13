from abc import ABC, abstractmethod
import pytz
import logging

_logger = logging.getLogger(__name__)

class BaseAttendanceAdapter(ABC):
    """Abstract base class for all attendance device adapters"""
    
    def __init__(self, device):
        self.device = device
        self.env = device.env
    
    @abstractmethod
    def test_connection(self):
        """Test connection to device"""
        pass
    
    @abstractmethod
    def get_attendance_logs(self, from_date=None, to_date=None):
        """Fetch attendance logs from device"""
        pass
    
    @abstractmethod
    def get_users(self):
        """Fetch users from device"""
        pass
    
    @abstractmethod
    def push_user(self, device_user):
        """Push user to device"""
        pass
    
    @abstractmethod
    def delete_user(self, device_user_id):
        """Delete user from device"""
        pass
    
    def normalize_timestamp(self, timestamp):
        """Convert device timestamp to UTC"""
        device_tz = pytz.timezone(self.device.timezone)
        if timestamp.tzinfo is None:
            timestamp = device_tz.localize(timestamp)
        return timestamp.astimezone(pytz.UTC).replace(tzinfo=None)