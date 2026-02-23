# idea
project_type: web_app
ai_task: image_upload
input_type: text_input
ui_type: web_interface
quality_mode: quality_first
quality_focus: visual_first
quality_simulation_level: medium
publish_gate: strict
auto_retry_on_fail: true
max_retry_count: 1
timeout_ms: 30000
retry_max_retries: 1
retry_backoff_ms: 500
api_method: POST
api_endpoint: /api/generate

## original_request
Build a web app where users upload images and admins review structured results.
