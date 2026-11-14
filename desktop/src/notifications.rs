//! System notifications, sent by the shell itself to `org.freedesktop.Notifications` on
//! the session bus (the page never gains a native permission; WebKit's own notification
//! request stays denied). One connection lives as long as the app: servers deliver action
//! signals only to the sender and close notifications when it disconnects.
/// What a click on a notification asks for: the notice's key, the action (`default` for
/// the body, `snooze` and `done` for the buttons) and the activation token that lets the
/// window raise.
pub type Invoked = dyn Fn(String, String, Option<String>) + Send + Sync;

#[cfg(target_os = "linux")]
pub use dbus::Notices;

/// Off Linux there is no D-Bus notification server: `connect` finds none and nothing is
/// sent. Native notifications there are part of the port (docs/windows-port.md). The type
/// cannot be made.
#[cfg(not(target_os = "linux"))]
pub struct Notices(std::convert::Infallible);

#[cfg(not(target_os = "linux"))]
impl Notices {
    pub fn connect(_invoked: std::sync::Arc<Invoked>) -> Option<Self> { None }
    pub fn notify(&self, _key: &str, _title: &str, _body: &str, _actions: bool) { match self.0 {} }
    pub fn close(&self, _keys: &[String]) { match self.0 {} }
}

#[cfg(target_os = "linux")]
mod dbus {
    use super::Invoked;
    use std::{collections::HashMap, sync::Arc, thread};

    use parking_lot::Mutex;
    use zbus::{blocking::{Connection, Proxy}, zvariant::Value};

    const NAME: &str = "org.freedesktop.Notifications";
    const PATH: &str = "/org/freedesktop/Notifications";

    #[derive(Default)]
    struct Shown {
        /// The server's id for each notice on screen, so a repeat replaces it in place.
        ids: HashMap<String, u32>,
        /// The token the server sends just before an action (Wayland focus).
        token: Option<(u32, String)>,
    }

    pub struct Notices {
        proxy: Proxy<'static>,
        shown: Arc<Mutex<Shown>>,
    }

    impl Notices {
        /// Connect and start listening for clicks. None when there is no session bus or no
        /// notification server: notifications are then simply not shown.
        pub fn connect(invoked: Arc<Invoked>) -> Option<Self> {
            let connection = Connection::session().ok()?;
            let proxy = Proxy::new(&connection, NAME, PATH, NAME).ok()?;
            let shown = Arc::new(Mutex::new(Shown::default()));
            let signals = proxy.receive_all_signals().ok()?;
            let state = shown.clone();
            thread::spawn(move || {
                for message in signals {
                    let header = message.header();
                    let Some(member) = header.member().map(|name| name.as_str()) else { continue };
                    let body = message.body();
                    match member {
                        "ActivationToken" => {
                            if let Ok((id, token)) = body.deserialize::<(u32, String)>() { state.lock().token = Some((id, token)); }
                        }
                        "ActionInvoked" => {
                            let Ok((id, action)) = body.deserialize::<(u32, String)>() else { continue };
                            let (key, token) = {
                                let mut shown = state.lock();
                                let key = shown.ids.iter().find(|(_, shown_id)| **shown_id == id).map(|(key, _)| key.clone());
                                let token = shown.token.take().filter(|(for_id, _)| *for_id == id).map(|(_, token)| token);
                                (key, token)
                            };
                            if let Some(key) = key { invoked(key, action, token); }
                        }
                        "NotificationClosed" => {
                            if let Ok((id, _reason)) = body.deserialize::<(u32, u32)>() { state.lock().ids.retain(|_, shown_id| *shown_id != id); }
                        }
                        _ => {}
                    }
                }
            });
            Some(Self { proxy, shown })
        }

        /// Show one notice, replacing the one already shown under the same key. Urgency is
        /// always normal, so the desktop's Do Not Disturb holds it.
        pub fn notify(&self, key: &str, title: &str, body: &str, actions: bool) {
            let replaces = self.shown.lock().ids.get(key).copied().unwrap_or(0);
            let mut buttons = vec!["default", "Open"];
            if actions { buttons.extend(["snooze", "Snooze 10 min", "done", "Done"]); }
            let hints: HashMap<&str, Value> = HashMap::from([
                ("urgency", Value::U8(1)),
                ("desktop-entry", Value::from(crate::DESKTOP_ENTRY)),
            ]);
            let sent: zbus::Result<u32> = self.proxy.call("Notify", &("rika!", replaces, crate::DESKTOP_ENTRY, title, body, buttons, hints, -1i32));
            if let Ok(id) = sent { self.shown.lock().ids.insert(key.to_string(), id); }
        }

        /// Take down the notices under these keys (read or cleared in the app).
        pub fn close(&self, keys: &[String]) {
            for key in keys {
                let id = self.shown.lock().ids.remove(key);
                if let Some(id) = id { let _: zbus::Result<()> = self.proxy.call("CloseNotification", &(id,)); }
            }
        }
    }
}
