#[derive(Clone, Copy, Debug, Default, PartialEq, Eq)]
enum Phase {
    #[default]
    Running,
    Checking,
    Verified,
    Draining,
    Installing,
    Restarting,
    Stopping,
    Stopped,
}

#[derive(Debug, PartialEq, Eq)]
pub enum QuitDecision {
    Stop,
    UpdateBusy,
    AlreadyStopping,
}

/// Serialized by Desktop's mutex; no update/quit decision uses separate atomics.
#[derive(Default)]
pub struct Lifecycle {
    phase: Phase,
}

impl Lifecycle {
    pub fn begin_update(&mut self) -> bool {
        self.transition(Phase::Running, Phase::Checking)
    }

    pub fn verified(&mut self) -> bool {
        self.transition(Phase::Checking, Phase::Verified)
    }

    pub fn begin_install(&mut self) -> bool {
        self.transition(Phase::Verified, Phase::Draining)
    }

    pub fn backend_drained(&mut self) -> bool {
        self.transition(Phase::Draining, Phase::Installing)
    }

    pub fn restart(&mut self) -> bool {
        self.transition(Phase::Installing, Phase::Restarting)
    }

    pub fn finish_update(&mut self) {
        if matches!(self.phase, Phase::Checking | Phase::Verified) {
            self.phase = Phase::Running;
        }
    }

    pub fn begin_quit(&mut self) -> QuitDecision {
        match self.phase {
            Phase::Running => {
                self.phase = Phase::Stopping;
                QuitDecision::Stop
            }
            Phase::Stopping | Phase::Stopped | Phase::Restarting => QuitDecision::AlreadyStopping,
            _ => QuitDecision::UpdateBusy,
        }
    }

    pub fn stopped(&mut self) {
        self.phase = Phase::Stopped;
    }

    pub fn monitor_backend(&self) -> bool {
        matches!(self.phase, Phase::Running | Phase::Checking | Phase::Verified)
    }

    pub fn can_exit(&self) -> bool {
        matches!(self.phase, Phase::Stopped | Phase::Restarting)
    }

    fn transition(&mut self, from: Phase, to: Phase) -> bool {
        if self.phase != from { return false; }
        self.phase = to;
        true
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn concurrent_checks_and_quit_cannot_race_update() {
        let mut lifecycle = Lifecycle::default();
        assert!(lifecycle.begin_update());
        assert!(!lifecycle.begin_update());
        assert_eq!(lifecycle.begin_quit(), QuitDecision::UpdateBusy);
        assert!(lifecycle.monitor_backend());
    }

    #[test]
    fn only_verified_download_can_drain_backend() {
        let mut lifecycle = Lifecycle::default();
        assert!(!lifecycle.begin_install());
        assert!(lifecycle.begin_update());
        assert!(!lifecycle.begin_install());
        assert!(lifecycle.verified());
        assert!(lifecycle.begin_install());
        assert!(!lifecycle.monitor_backend());
        assert!(!lifecycle.can_exit());
        assert!(!lifecycle.begin_install());
    }

    #[test]
    fn check_failure_or_user_decline_keeps_backend_running_and_retryable() {
        for verified in [false, true] {
            let mut lifecycle = Lifecycle::default();
            assert!(lifecycle.begin_update());
            if verified { assert!(lifecycle.verified()); }
            lifecycle.finish_update();
            assert!(lifecycle.monitor_backend());
            assert!(lifecycle.begin_update());
        }
    }

    #[test]
    fn installation_cannot_restart_until_backend_is_drained() {
        let mut lifecycle = Lifecycle::default();
        assert!(lifecycle.begin_update());
        assert!(lifecycle.verified());
        assert!(lifecycle.begin_install());
        assert!(!lifecycle.restart());
        assert!(lifecycle.backend_drained());
        // On install success OR failure we reopen the saved workspace by restart.
        assert!(lifecycle.restart());
        assert!(lifecycle.can_exit());
        assert!(!lifecycle.monitor_backend());
    }

    #[test]
    fn quit_is_serialized_and_only_exits_after_owned_backend_stops() {
        let mut lifecycle = Lifecycle::default();
        assert_eq!(lifecycle.begin_quit(), QuitDecision::Stop);
        assert_eq!(lifecycle.begin_quit(), QuitDecision::AlreadyStopping);
        assert!(!lifecycle.begin_update());
        assert!(!lifecycle.can_exit());
        lifecycle.stopped();
        assert!(lifecycle.can_exit());
    }
}
