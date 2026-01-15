from odoo import models, fields, api, _
from datetime import datetime, date
import logging

_logger = logging.getLogger(__name__)


class AttendanceDailyPunch(models.Model):
    _name = 'attendance.daily.punch'
    _description = 'Daily Punch Record'
    _order = 'date desc, employee_id'
    _rec_name = 'employee_id'

    employee_id = fields.Many2one(
        'hr.employee',
        string='Employee',
        required=True,
        ondelete='cascade',
        index=True
    )
    date = fields.Date(
        string='Date',
        required=True,
        index=True,
        default=fields.Date.today
    )
    shift_id = fields.Many2one(
        'attendance.shift',
        string='Shift'
    )

    # Punch Records
    check_in_time = fields.Datetime(string='Check In')
    check_in_log_id = fields.Many2one('attendance.raw.log', string='Check In Log')

    break_start_time = fields.Datetime(string='Break Start')
    break_start_log_id = fields.Many2one('attendance.raw.log', string='Break Start Log')

    break_end_time = fields.Datetime(string='Break End')
    break_end_log_id = fields.Many2one('attendance.raw.log', string='Break End Log')

    check_out_time = fields.Datetime(string='Check Out')
    check_out_log_id = fields.Many2one('attendance.raw.log', string='Check Out Log')

    overtime_in_time = fields.Datetime(string='Overtime In')
    overtime_in_log_id = fields.Many2one('attendance.raw.log', string='Overtime In Log')

    overtime_out_time = fields.Datetime(string='Overtime Out')
    overtime_out_log_id = fields.Many2one('attendance.raw.log', string='Overtime Out Log')

    # Calculated fields
    work_hours = fields.Float(string='Work Hours', compute='_compute_hours', store=True)
    break_hours = fields.Float(string='Break Hours', compute='_compute_hours', store=True)
    overtime_hours = fields.Float(string='Overtime Hours', compute='_compute_hours', store=True)
    total_hours = fields.Float(string='Total Hours', compute='_compute_hours', store=True)

    # Status
    is_complete = fields.Boolean(
        string='Complete',
        compute='_compute_is_complete',
        store=True
    )

    # Link to attendance record
    attendance_id = fields.Many2one(
        'hr.attendance',
        string='Attendance Record'
    )

    company_id = fields.Many2one(
        'res.company',
        string='Company',
        related='employee_id.company_id',
        store=True
    )

    _sql_constraints = [
        ('employee_date_unique', 'UNIQUE(employee_id, date)',
         'Only one daily punch record per employee per day!')
    ]

    @api.depends('check_in_time', 'check_out_time', 'break_start_time', 'break_end_time',
                 'overtime_in_time', 'overtime_out_time')
    def _compute_hours(self):
        for record in self:
            work_hours = 0.0
            break_hours = 0.0
            overtime_hours = 0.0

            # Calculate regular work hours
            if record.check_in_time and record.check_out_time:
                delta = record.check_out_time - record.check_in_time
                work_hours = delta.total_seconds() / 3600

            # Calculate break hours
            if record.break_start_time and record.break_end_time:
                delta = record.break_end_time - record.break_start_time
                break_hours = delta.total_seconds() / 3600

            # Calculate overtime hours
            if record.overtime_in_time and record.overtime_out_time:
                delta = record.overtime_out_time - record.overtime_in_time
                overtime_hours = delta.total_seconds() / 3600

            record.work_hours = work_hours
            record.break_hours = break_hours
            record.overtime_hours = overtime_hours
            record.total_hours = work_hours - break_hours + overtime_hours

    @api.depends('check_in_time', 'check_out_time')
    def _compute_is_complete(self):
        for record in self:
            record.is_complete = bool(record.check_in_time and record.check_out_time)

    def get_filled_slot_ids(self):
        """Get list of slot IDs that have been filled today"""
        self.ensure_one()
        filled = []

        if not self.shift_id or not self.shift_id.use_punch_slots:
            return filled

        # Map punch types to their slot IDs
        for slot in self.shift_id.punch_slot_ids:
            if slot.punch_type == '0' and self.check_in_time:
                filled.append(slot.id)
            elif slot.punch_type == '1' and self.check_out_time:
                filled.append(slot.id)
            elif slot.punch_type == '2' and self.break_start_time:
                filled.append(slot.id)
            elif slot.punch_type == '3' and self.break_end_time:
                filled.append(slot.id)
            elif slot.punch_type == '4' and self.overtime_in_time:
                filled.append(slot.id)
            elif slot.punch_type == '5' and self.overtime_out_time:
                filled.append(slot.id)

        return filled

    def record_punch(self, punch_type, timestamp, raw_log):
        """Record a punch for the specified type"""
        self.ensure_one()

        field_map = {
            '0': ('check_in_time', 'check_in_log_id'),
            '1': ('check_out_time', 'check_out_log_id'),
            '2': ('break_start_time', 'break_start_log_id'),
            '3': ('break_end_time', 'break_end_log_id'),
            '4': ('overtime_in_time', 'overtime_in_log_id'),
            '5': ('overtime_out_time', 'overtime_out_log_id'),
        }

        if punch_type in field_map:
            time_field, log_field = field_map[punch_type]
            self.write({
                time_field: timestamp,
                log_field: raw_log.id
            })

    @api.model
    def get_or_create_daily_record(self, employee, punch_date, shift=None):
        """Get or create daily punch record for an employee"""
        if isinstance(punch_date, datetime):
            punch_date = punch_date.date()

        record = self.search([
            ('employee_id', '=', employee.id),
            ('date', '=', punch_date)
        ], limit=1)

        if not record:
            record = self.create({
                'employee_id': employee.id,
                'date': punch_date,
                'shift_id': shift.id if shift else False,
            })

        return record