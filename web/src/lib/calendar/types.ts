/** Calendar records as the `/calendar` API returns them. */

export type Cal = {
  id: string;
  name: string;
  /** Theme palette slot `c1`..`c6`, or a #rrggbb kept from before slots or from Google. */
  color: string;
  subject_id?: number | null;
  timezone: string;
  visible: number;
  revision: number;
  google_id?: string;
};

export type Attendee = {
  email: string;
  name: string;
  response: string;
  optional?: boolean;
};

export type CalendarEvent = {
  id: string;
  revision: number;
  title: string;
  calendar_id: string;
  start: string;
  end: string;
  timezone: string;
  all_day: boolean;
  occurrence: string;
  series_start?: string;
  series_end?: string;
  rrule: string;
  exdates: string[];
  description: string;
  location: string;
  url: string;
  source_key?: string;
  source_url?: string;
  busy: boolean;
  privacy: string;
  reminders: number[];
  attendees: Attendee[];
  attachments: { title: string; url: string }[];
  event_type: string;
  google_options: Record<string, unknown>;
  subject_id?: number | null;
  /** Picked palette slot; null follows the calendar. Ignored while a subject is linked. */
  color?: string | null;
  undo_id?: number;
};
