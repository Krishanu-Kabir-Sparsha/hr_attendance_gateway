from .base_adapter import BaseAttendanceAdapter
from .zkteco_adapter import ZKTecoAdapter
from .api_adapter import RestAPIAdapter
from .webhook_adapter import WebhookAdapter

# Placeholder adapters
class HikvisionAdapter(BaseAttendanceAdapter):
    def test_connection(self): return False
    def get_attendance_logs(self, from_date=None, to_date=None): return []
    def get_users(self): return []
    def push_user(self, device_user): return False
    def delete_user(self, device_user_id): return False

class SupremaAdapter(BaseAttendanceAdapter):
    def test_connection(self): return False
    def get_attendance_logs(self, from_date=None, to_date=None): return []
    def get_users(self): return []
    def push_user(self, device_user): return False
    def delete_user(self, device_user_id): return False

class SoapAdapter(BaseAttendanceAdapter):
    def test_connection(self): return False
    def get_attendance_logs(self, from_date=None, to_date=None): return []
    def get_users(self): return []
    def push_user(self, device_user): return False
    def delete_user(self, device_user_id): return False