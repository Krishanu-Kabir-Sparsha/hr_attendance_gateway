from odoo import models, fields, api, _
from odoo.exceptions import ValidationError
from datetime import datetime
import pytz
import logging

_logger = logging.getLogger(__name__)


class AttendancePunchSlot(models.Model):
    _name = 'attendance.punch.slot'
    _description = 'Punch Time Slot'
    _order = 'shift_id, sequence, time_from'

    shift_id = fields.Many2one(
        'attendance.shift',
        string='Shift',
        required=True,
        ondelete='cascade'
    )
    name = fields.Char(string='Name', required=True)
    sequence = fields.Integer(string='Sequence', default=10)

    punch_type = fields.Selection([
        ('0', 'Check In'),
        ('1', 'Check Out'),
        ('2', 'Break Out'),
        ('3', 'Break In'),
        ('4', 'Overtime Start'),
        ('5', 'Overtime End'),
    ], string='Punch Type', required=True)

    time_from = fields.Float(
        string='Window Start',
        required=True,
        help='Start of time window. e.g., 7.0 = 07:00'
    )
    time_to = fields.Float(
        string='Window End',
        required=True,
        help='End of time window.e.g., 11.0 = 11:00'
    )
    
    is_required = fields.Boolean(
        string='Required',
        default=False,
        help='If checked, missing this punch may trigger alerts'
    )
    active = fields.Boolean(string='Active', default=True)

    # Display
    time_display = fields.Char(
        string='Time Window',
        compute='_compute_time_display'
    )
    punch_type_display = fields.Char(
        string='Type',
        compute='_compute_punch_type_display'
    )

    @api.depends('time_from', 'time_to')
    def _compute_time_display(self):
        for record in self:
            record.time_display = f"{self._float_to_time(record.time_from)} - {self._float_to_time(record.time_to)}"

    @api.depends('punch_type')
    def _compute_punch_type_display(self):
        type_labels = dict(self._fields['punch_type'].selection)
        for record in self:
            record.punch_type_display = type_labels.get(record.punch_type, '')

    @staticmethod
    def _float_to_time(float_time):
        """Convert float to HH:MM string"""
        hours = int(float_time) % 24
        minutes = int((float_time % 1) * 60)
        return f"{hours:02d}:{minutes:02d}"

    @api.constrains('time_from', 'time_to')
    def _check_times(self):
        for record in self:
            if record.time_from < 0 or record.time_from >= 24:
                raise ValidationError(_('Window start must be between 0 and 24'))
            if record.time_to < 0 or record.time_to > 24:
                raise ValidationError(_('Window end must be between 0 and 24'))

    def is_time_in_window(self, check_time, timezone='UTC'):
        """
        Check if the given datetime falls within this slot's time window.
        
        Args:
            check_time: datetime object (UTC)
            timezone: timezone string for the device/shift
            
        Returns:
            bool: True if time is within window
        """
        self.ensure_one()

        # Convert to local time
        tz = pytz.timezone(timezone)
        if check_time.tzinfo is None:
            check_time = pytz.UTC.localize(check_time)

        local_time = check_time.astimezone(tz)
        current_hour = local_time.hour + local_time.minute / 60.0 + local_time.second / 3600.0

        # Handle cross-midnight windows (e.g., 22:00 to 06:00)
        if self.time_to <= self.time_from:
            # Window crosses midnight
            return current_hour >= self.time_from or current_hour <= self.time_to
        else:
            # Normal window
            return self.time_from <= current_hour <= self.time_to