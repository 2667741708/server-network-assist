"""Keep the insertion fragment consistent with the canonical admin page."""
from pathlib import Path

root=Path(__file__).resolve().parents[1]/'src/server_network_assist/subscription_admin_ui'
page=(root/'subscriptions.html').read_text(encoding='utf8')
start=page.index('<section id="dashboard-panel"')
end=page.index('<div id="manager" hidden>',start)
modal_start=page.index('<dialog id="customer-manage-dialog"')
modal_end=page.index('</dialog>',modal_start)+len('</dialog>')
(root/'dashboard.html').write_text(page[start:end]+page[modal_start:modal_end]+'\n',encoding='utf8')
