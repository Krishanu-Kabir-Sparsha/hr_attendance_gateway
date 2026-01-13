from .base_adapter import BaseAttendanceAdapter

class WebhookAdapter(BaseAttendanceAdapter):
    """Webhook adapter - devices push data to us"""
    
    def test_connection(self):
        return True
    
    def get_attendance_logs(self, from_date=None, to_date=None):
        return []
    
    def get_users(self):
        return []
    
    def push_user(self, device_user):
        return False
    
    def delete_user(self, device_user_id):
        return False