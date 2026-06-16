app_name = "vobiz_click_to_call"
app_title = "Vobiz Click To Call"
app_publisher = "SRIAAS"
app_description = "Vobiz.ai click-to-call integration for Frappe/ERPNext"
app_email = "webdevelopersriaas@gmail.com"
app_license = "MIT"

after_install = "vobiz_click_to_call.install.after_install"
after_migrate = "vobiz_click_to_call.install.after_migrate"

app_include_js = [
    "/assets/vobiz_click_to_call/js/click_to_call.js",
    "/assets/vobiz_click_to_call/js/list_dialer.js",
    "/assets/vobiz_click_to_call/js/call_log.js",
]

doctype_js = {
    "Vobiz Settings": "public/js/vobiz_settings.js",
    "Vobiz User Mapping": "public/js/vobiz_user_mapping.js",
}

doc_events = {
    "Issue": {
        "on_trash": "vobiz_click_to_call.services.delete_cleanup.cleanup_issue_call_log_links",
    },
    "Vobiz Call Log": {
        "on_trash": "vobiz_click_to_call.services.delete_cleanup.cleanup_call_log_reverse_links",
        "on_update": "vobiz_click_to_call.services.ai.on_vobiz_call_log_update",
    },
}

scheduled_events = {
    "hourly": [
        "vobiz_click_to_call.services.cdr.enqueue_recent_cdr_sync",
    ],
}
