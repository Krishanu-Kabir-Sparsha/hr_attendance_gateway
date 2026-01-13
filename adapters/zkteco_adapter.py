from .base_adapter import BaseAttendanceAdapter
from odoo.exceptions import UserError
from odoo import _
import logging

_logger = logging.getLogger(__name__)

class ZKTecoAdapter(BaseAttendanceAdapter):
    """Adapter for ZKTeco devices"""
    
    def __init__(self, device):
        super().__init__(device)
        self.zk = None
        self.conn = None
    
    def _connect(self):
        """Establish connection"""
        if self.conn:
            return self.conn
        
        try:
            from zk import ZK
            self.zk = ZK(
                self.device.ip_address,
                port=self.device.port or 4370,
                timeout=10,
                password=int(self.device.password or 0)
            )
            self.conn = self.zk.connect()
            return self.conn
        except ImportError:
            raise UserError(_("pyzk library not installed. Install with: pip install pyzk"))
        except Exception as e:
            _logger.error(f"ZKTeco connection failed: {str(e)}")
            raise UserError(_("Connection failed: %s") % str(e))
    
    def _disconnect(self):
        """Close connection"""
        if self.conn:
            try:
                self.conn.disconnect()
            except:
                pass
            finally:
                self.conn = None
                self.zk = None
    
    def test_connection(self):
        """Test connection"""
        try:
            conn = self._connect()
            conn.get_firmware_version()
            self._disconnect()
            return True
        except:
            self._disconnect()
            return False
    
    def get_attendance_logs(self, from_date=None, to_date=None):
        """Fetch attendance logs"""
        logs = []
        try:
            conn = self._connect()
            attendances = conn.get_attendance()
            
            for att in attendances:
                if from_date and att.timestamp < from_date:
                    continue
                if to_date and att.timestamp > to_date:
                    continue
                
                normalized_time = self.normalize_timestamp(att.timestamp)
                
                logs.append({
                    'device_user_id': str(att.user_id),
                    'timestamp': normalized_time,
                    'punch_type': str(att.punch),
                    'raw_data': {
                        'status': att.status,
                        'uid': att.uid,
                    }
                })
            
            _logger.info(f"Fetched {len(logs)} logs from {self.device.name}")
        finally:
            self._disconnect()
        
        return logs
    
    def get_users(self):
        """Fetch users"""
        users = []
        try:
            conn = self._connect()
            device_users = conn.get_users()
            
            for user in device_users:
                users.append({
                    'device_user_id': str(user.uid),
                    'name': user.name,
                    'card_number': str(user.card) if user.card else None,
                })
        finally:
            self._disconnect()
        
        return users
    
    def push_user(self, device_user):
        """Push user to device"""
        try:
            conn = self._connect()
            conn.set_user(
                uid=int(device_user.device_user_id),
                name=device_user.employee_id.name[:24],
                privilege=0,
                password='',
                group_id='',
                user_id=device_user.device_user_id,
                card=int(device_user.card_number) if device_user.card_number else 0
            )
            return True
        finally:
            self._disconnect()
    
    def delete_user(self, device_user_id):
        """Delete user from device"""
        try:
            conn = self._connect()
            conn.delete_user(uid=int(device_user_id))
            return True
        finally:
            self._disconnect()