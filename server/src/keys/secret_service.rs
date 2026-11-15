//! The freedesktop Secret Service over D-Bus, reading and writing the same items as
//! Python's stack (keyring's `SecretService` backend over secretstorage):
//!
//! - the default collection (`/org/freedesktop/secrets/aliases/default`, created with label
//!   `Default` when it does not exist), unlocked first when it is locked (the prompt);
//! - items created with replace, label `Password for 'NAME' on 'SERVICE'`, attributes
//!   `{username, service, application: "Python keyring library"}` and content type
//!   `text/plain`; found by `{username, service}`, listed by `{service}`;
//! - available when `org.freedesktop.secrets` has an owner or is activatable, as
//!   keyring's `priority` decides; otherwise there is no keyring.
//!
//! Secrets travel in a `plain` session: the session bus admits only this user's processes,
//! which could read the unlocked collection themselves, so the transfer encryption adds no
//! boundary here (and would need a 1024-bit Diffie-Hellman and AES-CBC that the shell's crates
//! do not carry). Every value stays out of errors: a failure is only [`Fault`].

use std::collections::HashMap;

use zbus::blocking::{Connection, MessageIterator};
use zbus::message::Type as MessageType;
use zbus::zvariant::{ObjectPath, OwnedObjectPath, OwnedValue, Value};
use zbus::MatchRule;

use super::store::{Fault, Keyring};

const DBUS: &str = "org.freedesktop.DBus";
const DBUS_PATH: &str = "/org/freedesktop/DBus";
const BUS: &str = "org.freedesktop.secrets";
const SERVICE_PATH: &str = "/org/freedesktop/secrets";
const DEFAULT_COLLECTION: &str = "/org/freedesktop/secrets/aliases/default";
const SERVICE: &str = "org.freedesktop.Secret.Service";
const COLLECTION: &str = "org.freedesktop.Secret.Collection";
const ITEM: &str = "org.freedesktop.Secret.Item";
const PROMPT: &str = "org.freedesktop.Secret.Prompt";
/// keyring's `Keyring.appid`.
const APPLICATION: &str = "Python keyring library";
/// secretstorage's `ItemNotFoundException` errors.
const NOT_FOUND: &[&str] = &[
    "org.freedesktop.DBus.Error.UnknownMethod",
    "org.freedesktop.Secret.Error.NoSuchObject",
    "org.freedesktop.DBus.Error.UnknownObject",
];

type Secret = (OwnedObjectPath, Vec<u8>, Vec<u8>, String);

pub struct SecretService {
    connection: Connection,
    /// The `service` attribute kitty files its entries under.
    service: String,
    session: Option<OwnedObjectPath>,
}

impl From<zbus::Error> for Fault {
    fn from(_: zbus::Error) -> Self {
        Fault
    }
}

impl From<zbus::zvariant::Error> for Fault {
    fn from(_: zbus::zvariant::Error) -> Self {
        Fault
    }
}

fn not_found(error: &zbus::Error) -> bool {
    matches!(error, zbus::Error::MethodError(name, _, _) if NOT_FOUND.contains(&name.as_str()))
}

impl SecretService {
    /// The Secret Service on the session bus at `address` (`DBUS_SESSION_BUS_ADDRESS`), or
    /// None when there is no bus or no Secret Service on it (running or activatable).
    pub fn open(address: Option<&str>, service: &str) -> Option<Self> {
        // jeepney connects to the first address of the list it understands, with no fallback.
        let address = address?.split(';').find_map(|part| zbus::Address::try_from(part).ok())?;
        let connection = zbus::blocking::connection::Builder::address(address).ok()?.build().ok()?;
        let ring = Self { connection, service: service.to_owned(), session: None };
        ring.available().ok()?.then_some(ring)
    }

    /// `secretstorage.check_service_availability`.
    fn available(&self) -> Result<bool, Fault> {
        let owned = self.connection.call_method(Some(DBUS), DBUS_PATH, Some(DBUS), "NameHasOwner", &(BUS,))?;
        if owned.body().deserialize::<bool>()? {
            return Ok(true);
        }
        let names = self.connection.call_method(Some(DBUS), DBUS_PATH, Some(DBUS), "ListActivatableNames", &())?;
        Ok(names.body().deserialize::<Vec<String>>()?.iter().any(|name| name == BUS))
    }

    fn call<B>(&self, path: &str, interface: &str, method: &str, body: &B) -> zbus::Result<zbus::Message>
    where
        B: serde::Serialize + zbus::zvariant::DynamicType,
    {
        self.connection.call_method(Some(BUS), path, Some(interface), method, body)
    }

    fn property(&self, path: &str, interface: &str, name: &str) -> zbus::Result<OwnedValue> {
        let reply = self.call(path, "org.freedesktop.DBus.Properties", "Get", &(interface, name))?;
        Ok(reply.body().deserialize::<OwnedValue>()?)
    }

    fn locked(&self, path: &str, interface: &str) -> Result<bool, Fault> {
        Ok(bool::try_from(self.property(path, interface, "Locked")?)?)
    }

    /// `exec_prompt`: shows the prompt and waits for its `Completed` signal; true when dismissed.
    fn prompt(&self, prompt: &str) -> Result<(bool, OwnedValue), Fault> {
        let rule = MatchRule::builder().msg_type(MessageType::Signal).interface(PROMPT)?.member("Completed")?.path(prompt)?.build();
        let signals = MessageIterator::for_match_rule(rule, &self.connection, Some(16))?;
        self.call(prompt, PROMPT, "Prompt", &("",))?;
        for message in signals {
            let message = message?;
            let (dismissed, result): (bool, OwnedValue) = message.body().deserialize()?;
            return Ok((dismissed, result));
        }
        Err(Fault)
    }

    /// `unlock_objects`: the unlock prompt when the service asks for one.
    fn unlock(&self, path: &str) -> Result<(), Fault> {
        let paths = vec![ObjectPath::try_from(path)?];
        let (_unlocked, prompt): (Vec<OwnedObjectPath>, OwnedObjectPath) = self.call(SERVICE_PATH, SERVICE, "Unlock", &(paths,))?.body().deserialize()?;
        if prompt.as_str().len() > 1 {
            self.prompt(prompt.as_str())?;
        }
        Ok(())
    }

    /// keyring's `get_preferred_collection`: the default collection (made when missing),
    /// unlocked; a prompt the user dismissed leaves it locked, which fails.
    fn collection(&self) -> Result<String, Fault> {
        let path = match self.property(DEFAULT_COLLECTION, COLLECTION, "Label") {
            Ok(_) => DEFAULT_COLLECTION.to_owned(),
            Err(error) if not_found(&error) => self.create_default()?,
            Err(_) => return Err(Fault),
        };
        if self.locked(&path, COLLECTION)? {
            self.unlock(&path)?;
            if self.locked(&path, COLLECTION)? {
                return Err(Fault);
            }
        }
        Ok(path)
    }

    /// `create_collection(connection, 'Default', 'default')`.
    fn create_default(&self) -> Result<String, Fault> {
        let properties = HashMap::from([("org.freedesktop.Secret.Collection.Label", Value::from("Default"))]);
        let (path, prompt): (OwnedObjectPath, OwnedObjectPath) =
            self.call(SERVICE_PATH, SERVICE, "CreateCollection", &(properties, "default"))?.body().deserialize()?;
        if path.as_str().len() > 1 {
            return Ok(path.as_str().to_owned());
        }
        let (dismissed, result) = self.prompt(prompt.as_str())?;
        if dismissed {
            return Err(Fault);
        }
        Ok(OwnedObjectPath::try_from(result)?.as_str().to_owned())
    }

    fn session(&mut self) -> Result<OwnedObjectPath, Fault> {
        if let Some(session) = &self.session {
            return Ok(session.clone());
        }
        let (_output, session): (OwnedValue, OwnedObjectPath) =
            self.call(SERVICE_PATH, SERVICE, "OpenSession", &("plain", Value::from("")))?.body().deserialize()?;
        self.session = Some(session.clone());
        Ok(session)
    }

    fn search(&self, collection: &str, attributes: &HashMap<&str, &str>) -> Result<Vec<OwnedObjectPath>, Fault> {
        Ok(self.call(collection, COLLECTION, "SearchItems", &(attributes,))?.body().deserialize()?)
    }

    /// The first item filed under NAME (`Item()` reads its label, failing for a vanished one).
    fn find(&self, collection: &str, name: &str) -> Result<Option<OwnedObjectPath>, Fault> {
        let found = self.search(collection, &HashMap::from([("username", name), ("service", self.service.as_str())]))?;
        match found.into_iter().next() {
            Some(item) => {
                self.property(item.as_str(), ITEM, "Label")?;
                Ok(Some(item))
            }
            None => Ok(None),
        }
    }
}

impl Keyring for SecretService {
    fn get(&mut self, name: &str) -> Result<Option<String>, Fault> {
        let collection = self.collection()?;
        let Some(item) = self.find(&collection, name)? else { return Ok(None) };
        self.unlock(item.as_str())?;
        if self.locked(item.as_str(), ITEM)? {
            return Err(Fault);
        }
        let session = self.session()?;
        let (_session, _parameters, value, _kind): Secret = self.call(item.as_str(), ITEM, "GetSecret", &(session,))?.body().deserialize()?;
        String::from_utf8(value).map(Some).map_err(|_| Fault)
    }

    fn set(&mut self, name: &str, value: &str) -> Result<(), Fault> {
        let collection = self.collection()?;
        if self.locked(&collection, COLLECTION)? {
            return Err(Fault);
        }
        let session = self.session()?;
        let label = format!("Password for '{name}' on '{}'", self.service);
        let attributes = HashMap::from([("username", name), ("service", self.service.as_str()), ("application", APPLICATION)]);
        let properties = HashMap::from([
            ("org.freedesktop.Secret.Item.Label", Value::from(label)),
            ("org.freedesktop.Secret.Item.Attributes", Value::from(attributes)),
        ]);
        let secret: Secret = (session, Vec::new(), value.as_bytes().to_vec(), "text/plain".to_owned());
        let (item, prompt): (OwnedObjectPath, OwnedObjectPath) =
            self.call(&collection, COLLECTION, "CreateItem", &(properties, secret, true))?.body().deserialize()?;
        if item.as_str().len() > 1 {
            return Ok(());
        }
        match self.prompt(prompt.as_str())? {
            (false, _) => Ok(()),
            (true, _) => Err(Fault),
        }
    }

    fn delete(&mut self, name: &str) -> Result<bool, Fault> {
        let collection = self.collection()?;
        let Some(item) = self.find(&collection, name)? else { return Ok(false) };
        if self.locked(item.as_str(), ITEM)? {
            return Err(Fault);
        }
        let prompt: OwnedObjectPath = self.call(item.as_str(), ITEM, "Delete", &())?.body().deserialize()?;
        if prompt.as_str() != "/" && self.prompt(prompt.as_str())?.0 {
            return Err(Fault);
        }
        Ok(true)
    }

    fn names(&mut self) -> Result<Vec<String>, Fault> {
        let collection = self.collection()?;
        let mut names = Vec::new();
        for item in self.search(&collection, &HashMap::from([("service", self.service.as_str())]))? {
            self.property(item.as_str(), ITEM, "Label")?;
            let attributes = HashMap::<String, String>::try_from(self.property(item.as_str(), ITEM, "Attributes")?)?;
            names.push(attributes.get("username").cloned().unwrap_or_default());
        }
        Ok(names)
    }
}
