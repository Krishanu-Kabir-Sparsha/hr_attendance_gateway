from .base_adapter import BaseAttendanceAdapter
from odoo.exceptions import UserError
from odoo import _
import requests
import logging

_logger = logging.getLogger(__name__)

class RestAPIAdapter(BaseAttendanceAdapter):
    """Generic REST API adapter"""
    
    def __init__(self, device):
        super().__init__(device)
        self.session = requests.Session()
        
        if device.api_key:
            self.session.headers.update({
                'Authorization': f'Bearer {device.api_key}'
            })
        
        if device.username and device.password:
            self.session.auth = (device.username, device.password)
    
    def _make_request(self, method, endpoint, **kwargs):
        """Make HTTP request"""
        url = f"{self.device.api_url.rstrip('/')}/{endpoint.lstrip('/')}"
        
        try:
            response = self.session.request(method, url, timeout=30, **kwargs)
            response.raise_for_status()
            return response.json() if response.content else {}
        except requests.exceptions.RequestException as e:
            _logger.error(f"API request failed: {str(e)}")
            raise UserError(_("API request failed: %s") % str(e))
    
    def test_connection(self):
        """Test API connection"""
        try:
            self._make_request('GET', '/health')
            return True
        except:
            try:
                self._make_request('GET', '/')
                return True
            except:
                return False
    
    def get_attendance_logs(self, from_date=None, to_date=None):
        """Fetch attendance logs via API"""
        params = {}
        if from_date:
            params['from_date'] = from_date.isoformat()
        if to_date:
            params['to_date'] = to_date.isoformat()
        
        data = self._make_request('GET', '/attendance/logs', params=params)
        
        logs = []
        for item in data.get('logs', []):
            logs.append({
                'device_user_id': str(item['user_id']),
                'timestamp': item['timestamp'],
                'punch_type': str(item.get('type', '0')),
                'raw_data': item
            })
        
        return logs
    
    def get_users(self):
        """Fetch users via API"""
        data = self._make_request('GET', '/users')
        
        users = []
        for item in data.get('users', []):
            users.append({
                'device_user_id': str(item['id']),
                'name': item.get('name', ''),
                'card_number': item.get('card', '')
            })
        
        return users
    
    def push_user(self, device_user):
        """Push user via API"""
        payload = {
            'id': device_user.device_user_id,
            'name': device_user.employee_id.name,
            'card': device_user.card_number or ''
        }
        
        self._make_request('POST', '/users', json=payload)
        return True
    
    def delete_user(self, device_user_id):
        """Delete user via API"""
        self._make_request('DELETE', f'/users/{device_user_id}')
        return True