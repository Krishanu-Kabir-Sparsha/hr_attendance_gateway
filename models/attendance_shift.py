from odoo import models, fields, api, _
from odoo.exceptions import ValidationError
from datetime import datetime, timedelta, time
import pytz
import logging

_logger = logging.getLogger(__name__)


class AttendanceShift(models.Model):
    _name = 'attendance.shift'
    _description = 'Work Shift Configuration'
    _order = 'sequence, name'

    name = fields.Char(string='Shift Name', required=True)
    code = fields.Char(string='Code', required=True)
    sequence = fields.Integer(string='Sequence', default=10)
    active = fields.Boolean(string='Active', default=True)
    is_default = fields.Boolean(string='Default Shift', default=False)

    # ===========================================
    # WORK HOURS (Reference for late/early calculation)
    # ===========================================
    work_hour_from = fields.Float(
        string='Shift Start',
        default=9.0,
        required=True,
        help='Expected shift start time. e.g., 9.0 = 09:00 AM'
    )
    work_hour_to = fields.Float(
        string='Shift End',
        default=18.0,
        required=True,
        help='Expected shift end time.e.g., 18.0 = 06:00 PM'
    )
    expected_hours = fields.Float(
        string='Expected Hours',
        compute='_compute_shift_info',
        store=True
    )
    is_night_shift = fields.Boolean(
        string='Night Shift',
        compute='_compute_shift_info',
        store=True,
        help='Automatically detected if shift end is before shift start'
    )

    # ===========================================
    # ATTENDANCE STATUS RULES
    # These determine Late/Early/Overtime status
    # ===========================================
    late_after_minutes = fields.Integer(
        string='Late After (minutes)',
        default=15,
        help='Employee marked LATE if arrives this many minutes after shift start'
    )
    early_leave_before_minutes = fields.Integer(
        string='Early Leave Before (minutes)',
        default=15,
        help='Employee marked EARLY LEAVE if leaves this many minutes before shift end'
    )
    half_day_hours = fields.Float(
        string='Half Day Threshold (hours)',
        default=4.0,
        help='Mark as HALF DAY if worked less than this many hours'
    )
    overtime_after_hours = fields.Float(
        string='Overtime After (hours)',
        default=8.0,
        help='Overtime calculated for hours worked beyond this threshold'
    )

    # ===========================================
    # PUNCH PROCESSING RULES
    # These control how raw punches are processed
    # ===========================================
    min_punch_gap_minutes = fields.Float(
        string='Minimum Punch Gap (minutes)',
        default=1.0,
        help='Ignore punches within this time window (prevents duplicates from multiple finger attempts)'
    )
    auto_checkout_after_hours = fields.Float(
        string='Auto Checkout After (hours)',
        default=16.0,
        help='If no checkout after this many hours, auto-close attendance and treat next punch as new check-in'
    )

    # ===========================================
    # PUNCH SLOT SYSTEM (Optional Advanced Feature)
    # ===========================================
    use_punch_slots = fields.Boolean(
        string='Use Time-Based Punch Slots',
        default=False,
        help='''If enabled, punch type is determined by time of day: 
        - Punches during "Check In Window" → Check In
        - Punches during "Break Window" → Break Out/In
        - Punches during "Check Out Window" → Check Out
        
        If disabled, simple toggle logic is used: 
        - No open attendance → Check In
        - Has open attendance → Check Out'''
    )
    punch_slot_ids = fields.One2many(
        'attendance.punch.slot',
        'shift_id',
        string='Punch Time Slots'
    )

    # ===========================================
    # DISPLAY FIELDS
    # ===========================================
    work_time_display = fields.Char(
        string='Work Hours',
        compute='_compute_work_time_display'
    )
    company_id = fields.Many2one(
        'res.company',
        string='Company',
        default=lambda self: self.env.company
    )

    _sql_constraints = [
        ('code_company_unique', 'UNIQUE(code, company_id)',
         'Shift code must be unique per company!'),
    ]

    @api.depends('work_hour_from', 'work_hour_to')
    def _compute_shift_info(self):
        for record in self:
            if record.work_hour_to < record.work_hour_from:
                # Night shift (e.g., 22:00 to 06:00)
                record.is_night_shift = True
                record.expected_hours = (24 - record.work_hour_from) + record.work_hour_to
            else:
                record.is_night_shift = False
                record.expected_hours = record.work_hour_to - record.work_hour_from

    @api.depends('work_hour_from', 'work_hour_to')
    def _compute_work_time_display(self):
        for record in self:
            record.work_time_display = f"{self._float_to_time_str(record.work_hour_from)} - {self._float_to_time_str(record.work_hour_to)}"

    @staticmethod
    def _float_to_time_str(float_time):
        """Convert float (e.g., 9.5) to time string (09:30)"""
        hours = int(float_time)
        minutes = int((float_time - hours) * 60)
        return f"{hours: 02d}:{minutes:02d}"

    @api.constrains('is_default')
    def _check_single_default(self):
        for record in self:
            if record.is_default:
                others = self.search([
                    ('is_default', '=', True),
                    ('company_id', '=', record.company_id.id),
                    ('id', '!=', record.id)
                ])
                if others:
                    raise ValidationError(_('Only one default shift is allowed per company!'))

    @api.constrains('min_punch_gap_minutes')
    def _check_min_punch_gap(self):
        for record in self:
            if record.min_punch_gap_minutes < 0:
                raise ValidationError(_('Minimum punch gap cannot be negative'))

    @api.constrains('auto_checkout_after_hours')
    def _check_auto_checkout(self):
        for record in self:
            if record.auto_checkout_after_hours < 1:
                raise ValidationError(_('Auto checkout must be at least 1 hour'))

    def get_shift_boundaries(self, check_date, timezone='UTC'):
        """
        Get shift start/end datetime for a specific date.
        Used for calculating late/early status.
        
        Returns dict with:
        - shift_start: Expected shift start datetime (UTC, naive)
        - shift_end: Expected shift end datetime (UTC, naive)
        - late_threshold: Time after which employee is considered late
        - early_leave_threshold: Time before which leaving is considered early
        """
        self.ensure_one()

        if isinstance(check_date, datetime):
            check_date = check_date.date()

        tz = pytz.timezone(timezone)

        # Calculate shift start
        start_hour = int(self.work_hour_from)
        start_min = int((self.work_hour_from - start_hour) * 60)
        shift_start = tz.localize(datetime.combine(check_date, time(start_hour, start_min)))

        # Calculate shift end
        end_hour = int(self.work_hour_to)
        end_min = int((self.work_hour_to - end_hour) * 60)
        if self.is_night_shift:
            shift_end = tz.localize(datetime.combine(check_date + timedelta(days=1), time(end_hour, end_min)))
        else:
            shift_end = tz.localize(datetime.combine(check_date, time(end_hour, end_min)))

        # Convert to UTC naive for database comparison
        return {
            'shift_start': shift_start.astimezone(pytz.UTC).replace(tzinfo=None),
            'shift_end': shift_end.astimezone(pytz.UTC).replace(tzinfo=None),
            'late_threshold': (shift_start + timedelta(minutes=self.late_after_minutes)).astimezone(pytz.UTC).replace(tzinfo=None),
            'early_leave_threshold': (shift_end - timedelta(minutes=self.early_leave_before_minutes)).astimezone(pytz.UTC).replace(tzinfo=None),
        }

    def get_punch_type_for_time(self, punch_time, timezone='UTC'):
        """
        Determine punch type based on time of day (only if use_punch_slots=True).
        
        Args:
            punch_time: datetime of the punch
            timezone: timezone string
            
        Returns:
            - Punch type string ('0', '1', '2', etc.) if slot matches
            - None if no slot matches (fall back to toggle logic)
        """
        self.ensure_one()
        
        if not self.use_punch_slots:
            return None
        
        if not self.punch_slot_ids:
            return None
        
        # Check each slot in sequence order
        for slot in self.punch_slot_ids.filtered(lambda s: s.active).sorted('sequence'):
            if slot.is_time_in_window(punch_time, timezone):
                return slot.punch_type
        
        # No matching slot - return None to use toggle logic
        return None

    @api.model
    def get_employee_shift(self, employee):
        """Get the applicable shift for an employee"""
        # First check if employee has assigned shift
        if hasattr(employee, 'shift_id') and employee.shift_id:
            return employee.shift_id

        # Fall back to company default
        company_id = employee.company_id.id if employee.company_id else None
        return self.search([
            ('is_default', '=', True),
            ('company_id', 'in', [company_id, False])
        ], limit=1)

    def action_create_default_slots(self):
        """Create sensible default punch slots based on shift hours"""
        self.ensure_one()
        
        # Clear existing slots
        self.punch_slot_ids.unlink()
        
        # Calculate reasonable windows
        shift_start = self.work_hour_from
        shift_end = self.work_hour_to if not self.is_night_shift else self.work_hour_to + 24
        mid_day = (shift_start + shift_end) / 2
        
        slots_data = [
            {
                'name': 'Check In',
                'punch_type': '0',
                'time_from': max(0, shift_start - 2),  # 2 hours before shift
                'time_to': shift_start + 3,  # 3 hours after shift start
                'is_required': True,
                'sequence': 10,
            },
            {
                'name': 'Break Out',
                'punch_type': '2',
                'time_from': mid_day - 1,  # Around lunch time
                'time_to': mid_day + 1,
                'is_required': False,
                'sequence': 20,
            },
            {
                'name': 'Break In',
                'punch_type': '3',
                'time_from': mid_day - 0.5,
                'time_to': mid_day + 2,
                'is_required': False,
                'sequence': 30,
            },
            {
                'name': 'Check Out',
                'punch_type': '1',
                'time_from': shift_end - 1,  # 1 hour before shift end
                'time_to': min(24, shift_end + 4),  # 4 hours after shift end
                'is_required': True,
                'sequence': 40,
            },
        ]
        
        # Add overtime slots if shift allows
        if shift_end < 22: 
            slots_data.extend([
                {
                    'name': 'Overtime Start',
                    'punch_type': '4',
                    'time_from': shift_end,
                    'time_to': 23,
                    'is_required': False,
                    'sequence': 50,
                },
                {
                    'name': 'Overtime End',
                    'punch_type': '5',
                    'time_from': shift_end + 1,
                    'time_to': 23.99,
                    'is_required': False,
                    'sequence': 60,
                },
            ])
        
        for slot_data in slots_data:
            slot_data['shift_id'] = self.id
            # Normalize times to 0-24 range
            slot_data['time_from'] = slot_data['time_from'] % 24
            slot_data['time_to'] = slot_data['time_to'] % 24 if slot_data['time_to'] < 24 else slot_data['time_to'] - 24
            self.env['attendance.punch.slot'].create(slot_data)
        
        self.use_punch_slots = True
        
        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': _('Slots Created'),
                'message': _('Default punch slots have been created.Please review and adjust the time windows.'),
                'type': 'success',
            }
        }