(function () {
    const STATUSES = ["Available", "Away", "Offline"];
    const COLORS = {
        Available: "green",
        Busy: "orange",
        Away: "yellow",
        Offline: "gray",
    };

    let currentAvailability = null;

    function init() {
        if (!window.frappe || !frappe.session || frappe.session.user === "Guest") return;
        refresh();
    }

    function refresh() {
        frappe.call({
            method: "vobiz_click_to_call.api.call.get_my_availability",
        }).then((r) => {
            const data = r.message || {};
            if (!data.is_mapped) return;

            currentAvailability = data;
            ensureStyles();
            renderControl(data);
        });
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
                    background: var(--control-bg, #f7fafc);
                    border: 1px solid var(--border-color, #d1d8dd);
                    border-radius: 16px;
                    color: var(--text-color, #36414c);
                    cursor: pointer;
                    display: inline-flex;
                    font-size: 12px;
                    gap: 6px;
                    height: 28px;
                    padding: 0 10px;
                    white-space: nowrap;
                }
                .vobiz-availability-button:hover {
                    background: var(--btn-default-hover-bg, #eef2f7);
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
                    background: var(--card-bg, #fff);
                    border: 1px solid var(--border-color, #d1d8dd);
                    border-radius: 6px;
                    box-shadow: 0 8px 24px rgba(0, 0, 0, 0.12);
                    display: none;
                    min-width: 160px;
                    padding: 6px;
                    position: absolute;
                    right: 0;
                    top: 34px;
                    z-index: 1100;
                }
                .vobiz-availability-control.open .vobiz-availability-menu {
                    display: block;
                }
                .vobiz-availability-item {
                    align-items: center;
                    border-radius: 4px;
                    color: var(--text-color, #36414c);
                    cursor: pointer;
                    display: flex;
                    font-size: 13px;
                    gap: 8px;
                    padding: 7px 8px;
                    width: 100%;
                }
                .vobiz-availability-item:hover {
                    background: var(--control-bg, #f7fafc);
                }
                .vobiz-availability-item.active {
                    font-weight: 600;
                }
            </style>
        `);
    }

    function renderControl(data) {
        const $existing = $("#vobiz-availability-control");
        const $control = $existing.length ? $existing : makeControl();

        const status = data.availability_status || "Available";
        const color = COLORS[status] || "gray";
        $control.find(".vobiz-availability-label").text(__(status));
        $control
            .find(".vobiz-availability-dot")
            .removeClass("vobiz-dot-green vobiz-dot-orange vobiz-dot-yellow vobiz-dot-gray")
            .addClass(`vobiz-dot-${color}`);

        $control.find(".vobiz-availability-button").attr("title", data.reason || __("Vobiz availability"));
        $control.find(".vobiz-availability-item").removeClass("active");
        $control.find(`[data-status="${status}"]`).addClass("active");
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
                <button type="button" class="vobiz-availability-item" data-status="${status}">
                    <span class="vobiz-availability-dot vobiz-dot-${color}"></span>
                    <span>${__(status)}</span>
                </button>
            `);
            $item.on("click", () => setStatus(status));
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
        frappe.call({
            method: "vobiz_click_to_call.api.call.set_my_availability",
            args: { status },
            freeze: true,
            freeze_message: __("Updating availability..."),
        }).then((r) => {
            const data = r.message || {};
            currentAvailability = data;
            renderControl(data);
            $(document).trigger("vobiz_availability_changed", [data]);
            frappe.show_alert({
                message: __("Vobiz availability: {0}", [data.availability_status || status]),
                indicator: data.availability_status === "Available" ? "green" : "orange",
            });
        });
    }

    $(document).on("vobiz_refresh_availability", refresh);

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
