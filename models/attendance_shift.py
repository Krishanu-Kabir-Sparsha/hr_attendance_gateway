from odoo import models, fields, api, _
from odoo.exceptions import ValidationError
from datetime import datetime, timedelta, time
import pytz
import logging

_logger = logging.getLogger(__name__)


class AttendanceShift(models.Model):
    _name = 'attendance.shift'
    _description = 'Attendance Shift Configuration'
    _order = 'sequence, name'

    name = fields.Char(string='Shift Name', required=True)
    code = fields.Char(string='Code', required=True)
    sequence = fields.Integer(string='Sequence', default=10)
    active = fields.Boolean(string='Active', default=True)
    is_default = fields.Boolean(
        string='Default Shift',
        default=False,
        help='If checked, this shift configuration will be used as default'
    )

    # Work Hours (for reference and late/early calculation)
    work_hour_from = fields.Float(
        string='Expected Start Time',
        default=9.0,
        help='Expected shift start time (e.g., 9.0 = 09:00).Used to calculate late check-in.'
    )
    work_hour_to = fields.Float(
        string='Expected End Time',
        default=17.0,
        help='Expected shift end time (e.g., 17.0 = 17:00).Used to calculate early leave.'
    )

    # ============================================
    # PUNCH PROCESSING RULES (This is the key part!)
    # ============================================
    
    min_punch_interval = fields.Float(
        string='Minimum Punch Interval (minutes)',
        default=1.0,
        help='Minimum time between punches to avoid duplicates. Punches within this interval are ignored. Default: 1 minute'
    )

    min_work_hours = fields.Float(
        string='Minimum Work Duration (hours)',
        default=0.1,
        help='Minimum work duration before check-out is allowed.Default: 6 minutes (0.1 hours)'
    )

    max_work_hours = fields.Float(
        string='Maximum Work Duration (hours)',
        default=16.0,
        help='Maximum expected work duration in a single session. Default: 16 hours'
    )

    auto_close_hours = fields.Float(
        string='Auto-close After (hours)',
        default=20.0,
        help='Automatically close open attendances older than this. Default: 20 hours'
    )

    # ============================================
    # GRACE PERIODS (for late/early calculation)
    # ============================================
    
    grace_period_checkin = fields.Integer(
        string='Check-in Grace Period (minutes)',
        default=15,
        help='Minutes after shift start before being marked as late.Default: 15 minutes'
    )

    grace_period_checkout = fields.Integer(
        string='Check-out Grace Period (minutes)',
        default=15,
        help='Minutes before shift end allowed for checkout without early leave.Default: 15 minutes'
    )

    # Early/Late Windows
    early_checkin_allowed = fields.Integer(
        string='Early Check-in Window (minutes)',
        default=120,
        help='How early before shift start employees can check in.Default: 2 hours'
    )

    late_checkout_allowed = fields.Integer(
        string='Late Check-out Window (minutes)',
        default=240,
        help='How late after shift end employees can check out.Default: 4 hours'
    )

    # Overtime
    overtime_enabled = fields.Boolean(string='Enable Overtime Tracking', default=True)
    overtime_threshold = fields.Float(
        string='Overtime After (hours)',
        default=8.0,
        help='Work hours after which overtime is calculated'
    )

    # Cross-midnight handling
    is_night_shift = fields.Boolean(
        string='Night Shift',
        compute='_compute_is_night_shift',
        store=True,
        help='Automatically detected if shift crosses midnight'
    )

    # Display
    work_time_display = fields.Char(
        string='Work Time',
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
    def _compute_is_night_shift(self):
        for record in self:
            record.is_night_shift = record.work_hour_to < record.work_hour_from

    @api.depends('work_hour_from', 'work_hour_to')
    def _compute_work_time_display(self):
        for record in self:
            from_str = self._float_to_time_str(record.work_hour_from)
            to_str = self._float_to_time_str(record.work_hour_to)
            record.work_time_display = f"{from_str} - {to_str}"

    @staticmethod
    def _float_to_time_str(float_time):
        """Convert float time (e.g., 9.5) to time string (09:30)"""
        hours = int(float_time)
        minutes = int((float_time - hours) * 60)
        return f"{hours: 02d}:{minutes:02d}"

    def _float_to_timedelta(self, float_time):
        """Convert float time to timedelta"""
        hours = int(float_time)
        minutes = int((float_time - hours) * 60)
        return timedelta(hours=hours, minutes=minutes)

    @api.constrains('is_default')
    def _check_single_default(self):
        for record in self:
            if record.is_default:
                other_defaults = self.search([
                    ('is_default', '=', True),
                    ('company_id', '=', record.company_id.id),
                    ('id', '!=', record.id)
                ])
                if other_defaults: 
                    raise ValidationError(_('Only one default shift configuration is allowed per company'))

    @api.constrains('min_punch_interval')
    def _check_min_punch_interval(self):
        for record in self:
            if record.min_punch_interval < 0:
                raise ValidationError(_('Minimum punch interval cannot be negative'))

    @api.constrains('min_work_hours', 'max_work_hours', 'auto_close_hours')
    def _check_hours(self):
        for record in self:
            if record.min_work_hours < 0:
                raise ValidationError(_('Minimum work hours cannot be negative'))
            if record.max_work_hours < record.min_work_hours:
                raise ValidationError(_('Maximum work hours must be greater than minimum'))
            if record.auto_close_hours < record.max_work_hours:
                raise ValidationError(_('Auto-close hours should be greater than or equal to maximum work hours'))

    def get_shift_times_for_date(self, date, timezone='UTC'):
        """
        Get actual datetime objects for shift start/end on a specific date.
        Used for calculating late check-in, early leave, etc.
        """
        self.ensure_one()

        if isinstance(date, datetime):
            date = date.date()

        tz = pytz.timezone(timezone) if timezone else pytz.UTC

        # Shift start
        start_time = datetime.combine(date, time.min) + self._float_to_timedelta(self.work_hour_from)
        start_time = tz.localize(start_time)

        # Shift end
        if self.is_night_shift:
            end_time = datetime.combine(date + timedelta(days=1), time.min) + self._float_to_timedelta(self.work_hour_to)
        else:
            end_time = datetime.combine(date, time.min) + self._float_to_timedelta(self.work_hour_to)
        end_time = tz.localize(end_time)

        # Calculate windows (convert to UTC for comparison)
        return {
            'shift_start': start_time.astimezone(pytz.UTC).replace(tzinfo=None),
            'shift_end': end_time.astimezone(pytz.UTC).replace(tzinfo=None),
            'late_checkin_until': (start_time + timedelta(minutes=self.grace_period_checkin)).astimezone(pytz.UTC).replace(tzinfo=None),
            'early_checkout_from': (end_time - timedelta(minutes=self.grace_period_checkout)).astimezone(pytz.UTC).replace(tzinfo=None),
        }

    @api.model
    def get_default_shift(self, company_id=None):
        """Get the default shift for a company"""
        domain = [('is_default', '=', True)]
        if company_id: 
            domain.append(('company_id', 'in', [company_id, False]))
        return self.search(domain, limit=1)

    @api.model
    def get_employee_shift(self, employee):
        """
        Get the applicable shift configuration for an employee.
        Priority: Employee's shift -> Company default -> Global default
        """
        if employee.shift_id:
            return employee.shift_id

        company_id = employee.company_id.id if employee.company_id else None
        return self.get_default_shift(company_id)