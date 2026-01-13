from odoo import models, fields, api, _
from odoo.exceptions import UserError
import re
import logging

_logger = logging.getLogger(__name__)


class AttendanceDeviceUser(models.Model):
    _name = 'attendance.device.user'
    _description = 'Device User Mapping'
    _rec_name = 'device_user_id'

    device_id = fields.Many2one('attendance.device', string='Device', required=True, ondelete='cascade')
    employee_id = fields.Many2one('hr.employee', string='Employee', ondelete='cascade')
    device_user_id = fields.Char(string='Device User ID', required=True, help='ID from device or Badge ID')
    device_user_name = fields.Char(string='Device User Name')

    # Mapping confidence
    mapping_confidence = fields.Selection([
        ('high', 'High Confidence'),
        ('medium', 'Medium Confidence'),
        ('low', 'Low Confidence'),
        ('manual', 'Manual Mapping')
    ], string='Mapping Quality', readonly=True)

    mapping_method = fields.Char(string='Mapping Method', readonly=True,
                                 help='How this mapping was created')

    # Biometric data
    has_fingerprint = fields.Boolean(string='Has Fingerprint', default=False)
    has_face = fields.Boolean(string='Has Face Recognition', default=False)
    has_card = fields.Boolean(string='Has Access Card', default=False)
    card_number = fields.Char(string='Card Number')

    # Sync settings
    sync_to_device = fields.Boolean(string='Sync to Device', default=True)
    last_sync_date = fields.Datetime(string='Last Synced')

    active = fields.Boolean(string='Active', default=True)
    company_id = fields.Many2one('res.company', string='Company', related='device_id.company_id', store=True)

    _sql_constraints = [
        ('device_user_unique', 'UNIQUE(device_id, device_user_id)',
         'Device user ID must be unique per device!'),
    ]

    @api.constrains('device_id', 'employee_id')
    def _check_employee_device_unique(self):
        for record in self:
            if record.employee_id: 
                duplicate = self.search([
                    ('device_id', '=', record.device_id.id),
                    ('employee_id', '=', record.employee_id.id),
                    ('id', '!=', record.id)
                ])
                if duplicate:
                    raise UserError(_("Employee %s is already mapped to device user %s on this device") %
                                    (record.employee_id.name, duplicate.device_user_id))

    def name_get(self):
        result = []
        for record in self:
            if record.employee_id:
                name = f"{record.device_user_id} - {record.employee_id.name}"
            else:
                name = f"{record.device_user_id} - {record.device_user_name or 'Unmapped'}"
            result.append((record.id, name))
        return result

    def _find_employee_by_badge(self, badge_id, company_id=None):
        """
        Find employee by badge ID using multiple strategies.
        Safely checks if fields exist before searching.
        
        :param badge_id: The badge/ID to search for
        :param company_id: Optional company ID to filter by
        : return: tuple (employee record or None, match_method string or None)
        """
        Employee = self.env['hr.employee']
        employee_fields = Employee._fields
        
        company_domain = [('company_id', 'in', [company_id, False])] if company_id else []
        
        # Strategy 1: identification_id (Standard Odoo field - Employee ID/Badge)
        if 'identification_id' in employee_fields:
            employee = Employee.search([
                ('identification_id', '=', badge_id)
            ] + company_domain, limit=1)
            if employee:
                return employee, 'identification_id'
        
        # Strategy 2: barcode (May exist if hr_attendance is installed)
        if 'barcode' in employee_fields:
            employee = Employee.search([
                ('barcode', '=', badge_id)
            ] + company_domain, limit=1)
            if employee:
                return employee, 'barcode'
        
        # Strategy 3: pin (Attendance PIN - if exists)
        if 'pin' in employee_fields:
            employee = Employee.search([
                ('pin', '=', badge_id)
            ] + company_domain, limit=1)
            if employee:
                return employee, 'pin'
        
        return None, None

    @api.model
    def get_or_create_mapping(self, device, device_user_id):
        """Get existing mapping or create with auto-match by badge ID"""

        # First, check if mapping already exists
        mapping = self.search([
            ('device_id', '=', device.id),
            ('device_user_id', '=', device_user_id)
        ], limit=1)

        if mapping:
            return mapping

        # Try to find employee using safe method
        company_id = device.company_id.id if device.company_id else None
        employee, match_method = self._find_employee_by_badge(device_user_id, company_id)

        # Create mapping
        mapping_vals = {
            'device_id': device.id,
            'device_user_id': device_user_id,
            'device_user_name': f'User {device_user_id}',
        }

        if employee:
            mapping_vals.update({
                'employee_id': employee.id,
                'mapping_confidence': 'high',
                'mapping_method': f'Auto-matched by {match_method}'
            })

        return self.create(mapping_vals)

    def action_sync_to_device(self):
        """Push employee data to device"""
        for record in self:
            if not record.employee_id:
                raise UserError(_("Please map an employee first"))

            try:
                adapter = record.device_id._get_adapter()
                adapter.push_user(record)
                record.last_sync_date = fields.Datetime.now()

                return {
                    'type': 'ir.actions.client',
                    'tag': 'display_notification',
                    'params': {
                        'title': _('Success'),
                        'message': _('User synced to device successfully'),
                        'type': 'success',
                    }
                }
            except Exception as e:
                raise UserError(_("Failed to sync user: %s") % str(e))

    def action_auto_map_employees(self):
        """Enhanced auto-mapping with Badge ID priority"""
        mapped_count = 0
        high_confidence = 0
        medium_confidence = 0
        low_confidence = 0

        for record in self.filtered(lambda r: not r.employee_id):
            result = self._find_best_employee_match(
                record.device_id,
                record.device_user_id,
                record.device_user_name,
                record.card_number
            )

            if result['employee']: 
                record.write({
                    'employee_id': result['employee'].id,
                    'mapping_confidence': result['confidence'],
                    'mapping_method': result['method']
                })
                mapped_count += 1

                if result['confidence'] == 'high':
                    high_confidence += 1
                elif result['confidence'] == 'medium': 
                    medium_confidence += 1
                else: 
                    low_confidence += 1

        message = _('Mapped %d users to employees:\n- High confidence: %d\n- Medium confidence: %d\n- Low confidence: %d') % (
            mapped_count, high_confidence, medium_confidence, low_confidence
        )

        if low_confidence > 0:
            message += _('\n\nPlease review low confidence mappings!')

        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': _('Auto Mapping Complete'),
                'message': message,
                'type': 'success' if low_confidence == 0 else 'warning',
                'sticky': True
            }
        }

    def _find_best_employee_match(self, device, device_user_id, device_user_name, card_number):
        """Find best employee match using multiple strategies with safe field checking"""

        Employee = self.env['hr.employee']
        employee_fields = Employee._fields
        company_id = device.company_id.id if device.company_id else None
        company_domain = [('company_id', 'in', [company_id, False])] if company_id else []

        # PRIORITY 1: Match by identification_id (Badge ID - MOST IMPORTANT!)
        if 'identification_id' in employee_fields:
            employee = Employee.search([
                ('identification_id', '=', device_user_id)
            ] + company_domain, limit=1)

            if employee:
                return {
                    'employee': employee,
                    'confidence': 'high',
                    'method': 'Badge ID match (identification_id)'
                }

        # PRIORITY 2: Match by barcode (if field exists)
        if 'barcode' in employee_fields: 
            employee = Employee.search([
                ('barcode', '=', device_user_id)
            ] + company_domain, limit=1)

            if employee: 
                return {
                    'employee': employee,
                    'confidence': 'high',
                    'method': 'Barcode match'
                }

        # PRIORITY 3: Match by pin (if field exists)
        if 'pin' in employee_fields: 
            employee = Employee.search([
                ('pin', '=', device_user_id)
            ] + company_domain, limit=1)

            if employee:
                return {
                    'employee': employee,
                    'confidence': 'high',
                    'method': 'PIN match'
                }

        # PRIORITY 4: Extract badge from device_user_name (e.g., "NN-60910013")
        if device_user_name:
            numbers = re.findall(r'\d{6,}', device_user_name)  # Find numbers with 6+ digits
            for num in numbers:
                # Try identification_id
                if 'identification_id' in employee_fields:
                    employee = Employee.search([
                        ('identification_id', '=', num)
                    ] + company_domain, limit=1)
                    if employee:
                        return {
                            'employee': employee,
                            'confidence': 'high',
                            'method': f'Badge extracted from name ({num})'
                        }

                # Try barcode
                if 'barcode' in employee_fields:
                    employee = Employee.search([
                        ('barcode', '=', num)
                    ] + company_domain, limit=1)
                    if employee:
                        return {
                            'employee': employee,
                            'confidence': 'high',
                            'method': f'Barcode extracted from name ({num})'
                        }

        # PRIORITY 5: Match by card number (if we have other mappings with same card)
        if card_number and card_number != '0': 
            existing = self.search([
                ('card_number', '=', card_number),
                ('employee_id', '!=', False)
            ], limit=1)
            if existing:
                return {
                    'employee': existing.employee_id,
                    'confidence': 'medium',
                    'method': 'Card number match'
                }

        # PRIORITY 6: Match by name (if device_user_name exists)
        if device_user_name:
            # Clean the name (remove prefixes like "NN-")
            clean_name = re.sub(r'^[A-Z]{2,3}-', '', device_user_name)
            clean_name = self._clean_name(clean_name)

            if clean_name:
                # Exact name match
                employee = Employee.search([
                    ('name', '=ilike', clean_name)
                ] + company_domain, limit=1)
                if employee: 
                    return {
                        'employee': employee,
                        'confidence': 'medium',
                        'method': 'Exact name match'
                    }

                # Fuzzy name match - only if we have meaningful name parts
                name_parts = clean_name.split()
                if len(name_parts) >= 2:
                    # Search for employees containing all parts
                    domain = company_domain.copy()
                    for part in name_parts:
                        if len(part) > 2: # Skip very short parts
                            domain.append(('name', 'ilike', part))
                    
                    if len(domain) > len(company_domain):
                        employees = Employee.search(domain)
                        if len(employees) == 1:
                            return {
                                'employee': employees,
                                'confidence': 'low',
                                'method': 'Fuzzy name match'
                            }

        # No match found
        return {
            'employee': None,
            'confidence': None,
            'method': None
        }

    def _clean_name(self, name):
        """Clean name string for better matching"""
        if not name:
            return ''

        # Remove extra whitespace
        name = ' '.join(name.split())
        # Remove special characters but keep letters and spaces
        name = re.sub(r'[^\w\s]', '', name)
        # Remove standalone numbers
        name = re.sub(r'\b\d+\b', '', name)
        return name.strip()