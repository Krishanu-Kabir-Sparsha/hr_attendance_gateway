from odoo import http, _
from odoo.http import request
import json
import logging

_logger = logging.getLogger(__name__)

class AttendanceWebhookController(http.Controller):
    
    @http.route('/attendance/webhook/<string:token>', type='json', auth='none', csrf=False, methods=['POST'])
    def receive_attendance(self, token, **kwargs):
        """Webhook endpoint to receive attendance data"""
        try:
            # Find device by token
            device = request.env['attendance.device'].sudo().search([
                ('webhook_token', '=', token),
                ('state', '=', 'active')
            ], limit=1)
            
            if not device:
                return {'status': 'error', 'message': 'Invalid token'}
            
            # Get JSON data
            data = request.jsonrequest
            
            # Process attendance data
            logs = []
            if isinstance(data, dict):
                logs = data.get('logs', [data])
            elif isinstance(data, list):
                logs = data
            
            processor = request.env['attendance.processor'].sudo()
            result = processor.process_raw_logs(device, logs)
            
            return {
                'status': 'success',
                'processed': result['processed'],
                'failed': result['failed']
            }
            
        except Exception as e:
            _logger.error(f"Webhook processing error: {str(e)}")
            return {'status': 'error', 'message': str(e)}
    
    @http.route('/attendance/webhook/<string:token>/test', type='http', auth='none', csrf=False, methods=['GET'])
    def test_webhook(self, token):
        """Test webhook endpoint"""
        device = request.env['attendance.device'].sudo().search([
            ('webhook_token', '=', token)
        ], limit=1)
        
        if device:
            return json.dumps({'status': 'ok', 'device': device.name})
        else:
            return json.dumps({'status': 'error', 'message': 'Invalid token'})