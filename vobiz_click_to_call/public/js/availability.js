(function () {
    const AGENT_CONSOLE_PAGE_CACHE_VERSION = "20260723.3";
    const AGENT_CONSOLE_PAGE_CACHE_VERSION_KEY = "vobiz_agent_console_page_cache_version";
    try {
        if (window.localStorage) {
            const currentVersion = window.localStorage.getItem(AGENT_CONSOLE_PAGE_CACHE_VERSION_KEY);
            if (currentVersion !== AGENT_CONSOLE_PAGE_CACHE_VERSION) {
                window.localStorage.removeItem("_page:vobiz-agent-console");
                window.localStorage.setItem(
                    AGENT_CONSOLE_PAGE_CACHE_VERSION_KEY,
                    AGENT_CONSOLE_PAGE_CACHE_VERSION
                );
            }
        }
    } catch (e) {}

    const STATUSES = ["Available", "Away", "Offline"];
    const COLORS = {
        Available: "green",
        Busy: "orange",
        Away: "yellow",
        Offline: "gray",
    };
    const LABELS = {
        Away: "Break",
    };
    const ACTIVITY_HEARTBEAT_MS = 30 * 1000;
    const DEFAULT_ACTIVITY_IDLE_MS = 5 * 60 * 1000;
    const AVAILABILITY_REFRESH_MS = 60 * 1000;
    const ACTIVITY_TAB_KEY = "vobiz_agent_activity_tab_id";
    const ACTIVITY_LAST_KEY = "vobiz_agent_last_activity_at";
    const BREAK_STARTED_KEY = "vobiz_agent_break_started_at";
    const AVAILABILITY_EVENT_KEY = "vobiz_agent_availability_event";

    let currentAvailability = null;
    let activityTabId = null;
    let activityHeartbeatTimer = null;
    let activityIdleTimer = null;
    let breakTimer = null;
    let availabilityRefreshTimer = null;
    let availabilityRequest = null;
    let activityBound = false;
    let breakSyncBound = false;
    let trackingActivity = false;
    let idleInactive = false;
    let lastRouteKey = "";

    function currentRoute() {
        if (!window.frappe || !frappe.get_route) return [];
        return frappe.get_route() || [];
    }

    function isDeskHome() {
        return window.location && window.location.pathname === "/app/home";
    }

    function shouldLoadAvailability() {
        return window.location && window.location.pathname && window.location.pathname.indexOf("/app") === 0;
    }

    function init() {
        if (!window.frappe || !frappe.session || frappe.session.user === "Guest") return;
        bindBreakSyncEvents();
        if (!shouldLoadAvailability()) {
            stopActivityTracking(true);
            return;
        }
        refresh();
    }

    function refresh() {
        if (!window.frappe || !frappe.session || frappe.session.user === "Guest") return;
        if (!shouldLoadAvailability()) return;
        if (availabilityRequest) return availabilityRequest;
        availabilityRequest = frappe.call({
            method: "vobiz_click_to_call.api.call.get_my_availability",
        }).then((r) => {
            const data = r.message || {};
            if (!data.is_mapped) return;

            currentAvailability = data;
            ensureStyles();
            renderControl(data);
            startActivityTracking();
            startAvailabilityRefreshTimer();
        }).always(() => {
            availabilityRequest = null;
        });
        return availabilityRequest;
    }

    function safeNumber(value) {
        const number = Number(value);
        return Number.isFinite(number) ? number : 0;
    }

    function ensureStyles() {
        if ($("#vobiz-availability-style").length) return;

        $("head").append(`
            <style id="vobiz-availability-style">
                .vobiz-availability-control {
                    align-items: center;
                    display: inline-flex;
                    margin-left: 8px;
                    position: relative;
                }
                .vobiz-availability-button {
                    align-items: center;
                    background: linear-gradient(180deg, #ffffff 0%, #f7fafc 100%);
                    border: 1px solid #dbe4ee;
                    border-radius: 16px;
                    box-shadow: 0 1px 2px rgba(15, 23, 42, .06);
                    color: #243042;
                    cursor: pointer;
                    display: inline-flex;
                    font-size: 13px;
                    font-weight: 600;
                    gap: 7px;
                    height: 28px;
                    padding: 0 11px;
                    transition: background .15s ease, border-color .15s ease, box-shadow .15s ease;
                    white-space: nowrap;
                }
                .vobiz-availability-button:hover {
                    background: #fff;
                    border-color: #c9d6e2;
                    box-shadow: 0 4px 12px rgba(15, 23, 42, .08);
                }
                .vobiz-availability-dot {
                    border-radius: 50%;
                    display: inline-block;
                    height: 8px;
                    width: 8px;
                }
                .vobiz-dot-green { background: #22a06b; }
                .vobiz-dot-orange { background: #f59e0b; }
                .vobiz-dot-yellow { background: #d6a000; }
                .vobiz-dot-gray { background: #8d99a6; }
                .vobiz-availability-menu {
                    background: #fff;
                    border: 0;
                    border-radius: 14px;
                    box-shadow: 0 14px 34px rgba(15, 23, 42, 0.18);
                    display: none;
                    min-width: 170px;
                    padding: 8px;
                    position: absolute;
                    right: 0;
                    top: 36px;
                    z-index: 1100;
                }
                .vobiz-availability-control.open .vobiz-availability-menu {
                    display: block;
                }
                .vobiz-availability-item {
                    align-items: center;
                    appearance: none;
                    background: linear-gradient(180deg, #ffffff 0%, #f7fafc 100%);
                    border: 0 !important;
                    border-radius: 16px;
                    box-shadow: 0 1px 2px rgba(15, 23, 42, .06);
                    color: #344054;
                    cursor: pointer;
                    display: flex;
                    font-size: 13px;
                    font-weight: 600;
                    gap: 8px;
                    height: 30px;
                    line-height: 1.2;
                    margin: 3px 0;
                    outline: 0 !important;
                    padding: 0 11px;
                    text-align: left;
                    transition: background .15s ease, box-shadow .15s ease, color .15s ease;
                    width: 100%;
                }
                .vobiz-availability-item:focus,
                .vobiz-availability-item:active {
                    border: 0 !important;
                    box-shadow: 0 1px 2px rgba(15, 23, 42, .06) !important;
                    outline: 0 !important;
                }
                .vobiz-availability-item:hover {
                    background: #fff;
                    box-shadow: 0 4px 12px rgba(15, 23, 42, .08);
                }
                .vobiz-availability-item.active {
                    background: #eef8f3;
                    color: #17694a;
                    font-weight: 600;
                }
                .vobiz-break-lock {
                    align-items: center;
                    background: rgba(248, 250, 252, .94);
                    backdrop-filter: blur(4px);
                    bottom: 0;
                    display: none;
                    justify-content: center;
                    left: 0;
                    padding: 24px;
                    position: fixed;
                    right: 0;
                    top: 0;
                    z-index: 99999;
                }
                .vobiz-break-lock.active {
                    display: flex;
                }
                .vobiz-break-panel {
                    background: #fff;
                    border-radius: 12px;
                    box-shadow: 0 24px 60px rgba(15, 23, 42, .18);
                    max-width: 420px;
                    padding: 28px;
                    text-align: center;
                    width: min(420px, 100%);
                }
                .vobiz-break-status {
                    align-items: center;
                    color: #17694a;
                    display: inline-flex;
                    font-size: 13px;
                    font-weight: 700;
                    gap: 8px;
                    margin-bottom: 18px;
                }
                .vobiz-break-title {
                    color: #1f2937;
                    font-size: 20px;
                    font-weight: 700;
                    margin-bottom: 8px;
                }
                .vobiz-break-subtitle {
                    color: #667085;
                    font-size: 13px;
                    line-height: 1.5;
                    margin-bottom: 20px;
                }
                .vobiz-break-timer {
                    color: #111827;
                    font-size: 42px;
                    font-variant-numeric: tabular-nums;
                    font-weight: 700;
                    line-height: 1;
                    margin-bottom: 24px;
                }
                .vobiz-break-online {
                    align-items: center;
                    background: #009b7d;
                    border: 0;
                    border-radius: 8px;
                    box-shadow: 0 8px 18px rgba(0, 155, 125, .22);
                    color: #fff;
                    cursor: pointer;
                    display: inline-flex;
                    font-size: 14px;
                    font-weight: 700;
                    gap: 8px;
                    height: 38px;
                    justify-content: center;
                    min-width: 150px;
                    outline: 0;
                    padding: 0 16px;
                }
                .vobiz-break-online:hover {
                    background: #00866c;
                }
                .vobiz-break-online:disabled {
                    cursor: not-allowed;
                    opacity: .7;
                }
            </style>
        `);
    }

    function renderControl(data) {
        const $existing = $("#vobiz-availability-control");
        const $control = $existing.length ? $existing : makeControl();

        const status = data.availability_status || "Available";
        const color = COLORS[status] || "gray";
        $control.find(".vobiz-availability-label").text(status_label(status));
        $control
            .find(".vobiz-availability-dot")
            .removeClass("vobiz-dot-green vobiz-dot-orange vobiz-dot-yellow vobiz-dot-gray")
            .addClass(`vobiz-dot-${color}`);

        $control.find(".vobiz-availability-button").attr("title", data.reason || __("Vobiz availability"));
        syncMenuLabels($control);
        $control.find(".vobiz-availability-item").removeClass("active");
        $control.find(`[data-status="${status}"]`).addClass("active");
        updateBreakLock(status, data);
    }

    function syncMenuLabels($control) {
        STATUSES.forEach((status) => {
            const $item = $control.find(`[data-status="${status}"]`);
            const $label = $item.find(".vobiz-availability-item-label");
            if ($label.length) {
                $label.text(status_label(status));
            } else {
                $item.children("span").last().text(status_label(status));
            }
        });
    }

    function makeControl() {
        const $control = $(`
            <div class="vobiz-availability-control" id="vobiz-availability-control">
                <button type="button" class="vobiz-availability-button">
                    <span class="vobiz-availability-dot"></span>
                    <span class="vobiz-availability-label"></span>
                    <i class="fa fa-angle-down"></i>
                </button>
                <div class="vobiz-availability-menu"></div>
            </div>
        `);

        STATUSES.forEach((status) => {
            const color = COLORS[status] || "gray";
            const $item = $(`
                <div role="button" tabindex="0" class="vobiz-availability-item" data-status="${status}">
                    <span class="vobiz-availability-dot vobiz-dot-${color}"></span>
                    <span class="vobiz-availability-item-label">${status_label(status)}</span>
                </div>
            `);
            $item.on("click", () => setStatus(status));
            $item.on("keydown", (event) => {
                if (event.key === "Enter" || event.key === " ") {
                    event.preventDefault();
                    setStatus(status);
                }
            });
            $control.find(".vobiz-availability-menu").append($item);
        });

        $control.find(".vobiz-availability-button").on("click", (event) => {
            event.preventDefault();
            event.stopPropagation();
            $control.toggleClass("open");
        });

        $(document).on("click.vobiz-availability", () => {
            $control.removeClass("open");
        });

        const $target = $(".navbar .navbar-nav").last();
        if ($target.length) {
            const $li = $('<li class="nav-item"></li>').append($control);
            $target.prepend($li);
        } else {
            $control.css({
                position: "fixed",
                right: "58px",
                top: "10px",
                zIndex: 1100,
            });
            $("body").append($control);
        }

        return $control;
    }

    function setStatus(status) {
        return frappe.call({
            method: "vobiz_click_to_call.api.call.set_my_availability",
            args: { status },
            freeze: true,
            freeze_message: __("Updating availability..."),
        }).then((r) => {
            const data = r.message || {};
            currentAvailability = data;
            renderControl(data);
            $(document).trigger("vobiz_availability_changed", [data]);
            broadcastAvailabilityChange(data);
            frappe.show_alert({
                message: __("Vobiz availability: {0}", [status_label(data.availability_status || status)]),
                indicator: data.availability_status === "Available" ? "green" : "orange",
            });
        });
    }

    function status_label(status) {
        return __(LABELS[status] || status || "");
    }

    function breakStorageKey() {
        const user = (frappe.session && frappe.session.user) || "Guest";
        return `${BREAK_STARTED_KEY}:${user}`;
    }

    function availabilityEventKey() {
        const user = (frappe.session && frappe.session.user) || "Guest";
        return `${AVAILABILITY_EVENT_KEY}:${user}`;
    }

    function activityLastKey() {
        const user = (frappe.session && frappe.session.user) || "Guest";
        return `${ACTIVITY_LAST_KEY}:${user}`;
    }

    function broadcastAvailabilityChange(data) {
        try {
            if (!window.localStorage) return;
            window.localStorage.setItem(availabilityEventKey(), JSON.stringify({
                status: data && data.availability_status,
                at: Date.now(),
            }));
        } catch (e) {}
    }

    function breakStartedAt(serverStartedAt) {
        try {
            const key = breakStorageKey();
            if (serverStartedAt) {
                const value = String(serverStartedAt);
                if (window.localStorage) window.localStorage.setItem(key, value);
                return serverStartedAt;
            }
            let value = window.localStorage && window.localStorage.getItem(key);
            if (!value) {
                value = String(Date.now());
                window.localStorage.setItem(key, value);
            }
            return safeNumber(value) || Date.now();
        } catch (e) {
            return Date.now();
        }
    }

    function clearBreakStartedAt() {
        try {
            if (window.localStorage) window.localStorage.removeItem(breakStorageKey());
        } catch (e) {}
    }

    function bindBreakSyncEvents() {
        if (breakSyncBound) return;
        breakSyncBound = true;
        $(window).on("storage.vobiz-break-sync", (event) => {
            const original = event.originalEvent || event;
            if (!original || original.key !== breakStorageKey()) return;
            if (original.newValue) {
                showBreakLock(safeNumber(original.newValue) || Date.now());
                if (currentAvailability) {
                    currentAvailability.availability_status = "Away";
                    currentAvailability.accept_calls = false;
                    renderControl(currentAvailability);
                } else {
                    refresh();
                }
                return;
            }
            hideBreakLock();
            refresh();
            $(document).trigger("vobiz_availability_changed", [currentAvailability || {}]);
        });
        $(window).on("storage.vobiz-availability-sync", (event) => {
            const original = event.originalEvent || event;
            if (!original || original.key !== availabilityEventKey()) return;
            refresh();
            $(document).trigger("vobiz_availability_changed", [currentAvailability || {}]);
        });
    }

    function makeBreakLock() {
        const $existing = $("#vobiz-break-lock");
        if ($existing.length) return $existing;

        const $lock = $(`
            <div class="vobiz-break-lock" id="vobiz-break-lock" aria-live="polite">
                <div class="vobiz-break-panel">
                    <div class="vobiz-break-status">
                        <span class="vobiz-availability-dot vobiz-dot-green"></span>
                        <span>${__("Break")}</span>
                    </div>
                    <div class="vobiz-break-title">${__("You are on break")}</div>
                    <div class="vobiz-break-subtitle">${__("CRM access will resume when you go online.")}</div>
                    <div class="vobiz-break-timer">00:00:00</div>
                    <button type="button" class="vobiz-break-online">
                        <i class="fa fa-check"></i>
                        <span>${__("Go Online")}</span>
                    </button>
                </div>
            </div>
        `);
        $lock.find(".vobiz-break-online").on("click", () => {
            const $button = $lock.find(".vobiz-break-online");
            $button.prop("disabled", true);
            Promise.resolve(setStatus("Available")).finally(() => {
                $button.prop("disabled", false);
            });
        });
        $("body").append($lock);
        return $lock;
    }

    function updateBreakLock(status, data) {
        if (status === "Away") {
            showBreakLock(breakStartedAt(safeNumber(data && data.last_status_epoch_ms)));
            return;
        }
        clearBreakStartedAt();
        hideBreakLock();
    }

    function startAvailabilityRefreshTimer() {
        clearInterval(availabilityRefreshTimer);
        availabilityRefreshTimer = setInterval(() => {
            if (!shouldLoadAvailability() || document.visibilityState === "hidden") return;
            if (currentAvailability && currentAvailability.availability_status === "Away") {
                showBreakLock(breakStartedAt(safeNumber(currentAvailability.last_status_epoch_ms)));
            }
            refresh();
        }, AVAILABILITY_REFRESH_MS);
    }

    function showBreakLock(startedAt) {
        ensureStyles();
        const $lock = makeBreakLock();
        $lock.addClass("active");
        startBreakTimer(startedAt || Date.now());
    }

    function hideBreakLock() {
        clearInterval(breakTimer);
        breakTimer = null;
        $("#vobiz-break-lock").removeClass("active");
    }

    function startBreakTimer(startedAt) {
        clearInterval(breakTimer);
        const update = () => {
            const elapsed = Math.max(0, Math.floor((Date.now() - startedAt) / 1000));
            const hours = String(Math.floor(elapsed / 3600)).padStart(2, "0");
            const minutes = String(Math.floor((elapsed % 3600) / 60)).padStart(2, "0");
            const seconds = String(elapsed % 60).padStart(2, "0");
            $("#vobiz-break-lock .vobiz-break-timer").text(`${hours}:${minutes}:${seconds}`);
        };
        update();
        breakTimer = setInterval(update, 1000);
    }

    function getActivityTabId() {
        if (activityTabId) return activityTabId;
        try {
            activityTabId = window.sessionStorage && window.sessionStorage.getItem(ACTIVITY_TAB_KEY);
            if (!activityTabId) {
                activityTabId = `desk-${Date.now()}-${Math.random().toString(36).slice(2)}`;
                window.sessionStorage.setItem(ACTIVITY_TAB_KEY, activityTabId);
            }
            return activityTabId;
        } catch (e) {
            activityTabId = `desk-${Date.now()}-${Math.random().toString(36).slice(2)}`;
            return activityTabId;
        }
    }

    function routeKey() {
        const route = currentRoute();
        return route.length ? route.join("/") : ((window.location && window.location.pathname) || "");
    }

    function startActivityTracking() {
        if (!currentAvailability || !currentAvailability.is_mapped || !shouldLoadAvailability()) return;
        bindActivityEvents();
        const wasTracking = trackingActivity;
        trackingActivity = true;
        lastRouteKey = routeKey();
        if (idleInactive && currentAvailability.availability_status !== "Away") {
            return;
        }
        if (!wasTracking && !idleInactive) {
            touchGlobalActivity();
        }
        if (currentAvailability.availability_status !== "Away") {
            recordActivity();
        }
        resetActivityIdleTimer();
        clearInterval(activityHeartbeatTimer);
        activityHeartbeatTimer = setInterval(() => {
            if (canRecordActivity()) {
                recordActivity();
            }
        }, ACTIVITY_HEARTBEAT_MS);
    }

    function canRecordActivity() {
        const idleRuleEnabled = isIdleAutoOfflineEnabled();
        return Boolean(
            trackingActivity &&
            currentAvailability &&
            currentAvailability.is_mapped &&
            shouldLoadAvailability() &&
            document.visibilityState !== "hidden" &&
            currentAvailability.availability_status !== "Away" &&
            (!idleRuleEnabled || globalActivityAge() < activityIdleMs()) &&
            !idleInactive
        );
    }

    function recordActivity() {
        if (!canRecordActivity()) return;
        frappe.call({
            method: "vobiz_click_to_call.api.console.record_agent_activity",
            type: "POST",
            freeze: false,
            args: {
                tab_id: getActivityTabId(),
                route: routeKey(),
            },
        });
    }

    function stopActivityTracking(closeSession) {
        clearInterval(activityHeartbeatTimer);
        clearTimeout(activityIdleTimer);
        activityHeartbeatTimer = null;
        activityIdleTimer = null;
        if (closeSession && trackingActivity && currentAvailability && currentAvailability.is_mapped) {
            markActivityInactive();
        }
        trackingActivity = false;
    }

    function markActivityInactive() {
        if (!isIdleAutoOfflineEnabled()) {
            return;
        }
        if (isIdleAutoOfflinePaused()) {
            return;
        }
        idleInactive = true;
        const url = "/api/method/vobiz_click_to_call.api.console.mark_agent_activity_inactive";
        if (window.fetch) {
            const body = new URLSearchParams();
            body.set("tab_id", getActivityTabId());
            fetch(url, {
                method: "POST",
                keepalive: true,
                headers: { "X-Frappe-CSRF-Token": frappe.csrf_token || "" },
                body,
                credentials: "same-origin",
            }).catch(() => {});
            return;
        }
        frappe.call({
            method: "vobiz_click_to_call.api.console.mark_agent_activity_inactive",
            type: "POST",
            freeze: false,
            args: { tab_id: getActivityTabId() },
        });
    }

    function bindActivityEvents() {
        if (activityBound) return;
        activityBound = true;
        const events = "mousemove.vobiz-agent-activity keydown.vobiz-agent-activity click.vobiz-agent-activity scroll.vobiz-agent-activity touchstart.vobiz-agent-activity";
        $(document).on(events, noteActivity);
        $(document).on("visibilitychange.vobiz-agent-activity", handleVisibilityChange);
        $(window).on("beforeunload.vobiz-agent-activity pagehide.vobiz-agent-activity", () => stopActivityTracking(false));
    }

    function noteActivity() {
        if (!currentAvailability || !currentAvailability.is_mapped || !shouldLoadAvailability()) return;
        touchGlobalActivity();
        if (currentAvailability.availability_status === "Away") {
            resetActivityIdleTimer();
            return;
        }
        if (idleInactive) {
            idleInactive = false;
            trackingActivity = true;
            recordActivity();
            clearInterval(activityHeartbeatTimer);
            activityHeartbeatTimer = setInterval(() => {
                if (canRecordActivity()) {
                    recordActivity();
                }
            }, ACTIVITY_HEARTBEAT_MS);
        }
        resetActivityIdleTimer();
    }

    function resetActivityIdleTimer() {
        clearTimeout(activityIdleTimer);
        if (!trackingActivity || idleInactive || !shouldLoadAvailability()) return;
        if (!isIdleAutoOfflineEnabled()) return;
        const age = globalActivityAge();
        const delay = Math.max(1000, activityIdleMs() - age);
        activityIdleTimer = setTimeout(() => {
            const age = globalActivityAge();
            if (age < activityIdleMs()) {
                resetActivityIdleTimer();
                return;
            }
            if (isIdleAutoOfflinePaused()) {
                touchGlobalActivity();
                resetActivityIdleTimer();
                return;
            }
            stopActivityTracking(false);
            markActivityInactive();
            if (currentAvailability) {
                currentAvailability.availability_status = "Offline";
                currentAvailability.accept_calls = false;
                renderControl(currentAvailability);
                broadcastAvailabilityChange(currentAvailability);
                $(document).trigger("vobiz_availability_changed", [currentAvailability || {}]);
            }
        }, delay);
    }

    function touchGlobalActivity() {
        try {
            if (window.localStorage) {
                window.localStorage.setItem(activityLastKey(), String(Date.now()));
            }
        } catch (e) {}
    }

    function globalActivityAge() {
        try {
            const value = window.localStorage && window.localStorage.getItem(activityLastKey());
            const last = safeNumber(value);
            if (!last) return activityIdleMs() + 1;
            return Math.max(0, Date.now() - last);
        } catch (e) {
            return 0;
        }
    }

    function isIdleAutoOfflineEnabled() {
        return !currentAvailability || currentAvailability.idle_auto_offline_enabled !== false;
    }

    function activityIdleMs() {
        const seconds = currentAvailability ? safeNumber(currentAvailability.idle_auto_offline_seconds) : 0;
        return Math.max(60 * 1000, (seconds || DEFAULT_ACTIVITY_IDLE_MS / 1000) * 1000);
    }

    function isIdleAutoOfflinePaused() {
        return Boolean(
            currentAvailability &&
            ["Away", "Busy"].includes(currentAvailability.availability_status)
        );
    }

    function handleVisibilityChange() {
        if (document.visibilityState === "hidden") {
            stopActivityTracking(false);
            return;
        }
        if (currentAvailability && currentAvailability.is_mapped && shouldLoadAvailability()) {
            startActivityTracking();
        }
    }

    function handleRouteChange() {
        const nextRouteKey = routeKey();
        if (lastRouteKey && lastRouteKey !== nextRouteKey) {
            stopActivityTracking(false);
        }
        init();
    }

    $(document).on("vobiz_refresh_availability", refresh);
    $(document).on("page-change route-change", handleRouteChange);

    if (frappe.ready) {
        frappe.ready(init);
    } else {
        $(init);
    }

    window.vobiz_click_to_call = window.vobiz_click_to_call || {};
    window.vobiz_click_to_call.get_availability = function () {
        return currentAvailability;
    };
})();
