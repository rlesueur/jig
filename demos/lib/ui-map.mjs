/*
 * Every element the demos touch, located only by accessible role and name, label text or data-testid
 * (plus the data attributes the UI puts on an item to say what it is, such as an approval's tool and status).
 * No CSS classes, ids used for styling, or layout positions, so a restyle of the UI does not break the
 * recordings. If the UI renames something, change it here (and nowhere else).
 *
 * This map follows the chat-first UI (one conversation view, approval cards inside the conversation,
 * "What Jig's up to" and Settings). Each entry is a function of the Playwright page (or a scope) returning
 * one locator. There are no alternatives or fallbacks: if an element cannot be found, the run fails and
 * says which one.
 */

const exact = { exact: true };
const tid = (p, id) => p.getByTestId(id);

export const ui = {
  /* sign-in */
  loginDialog: (p) => p.getByRole('dialog', { name: 'Sign in to Jig' }),
  tokenField: (p) => ui.loginDialog(p).getByLabel('API token', exact),
  signIn: (p) => ui.loginDialog(p).getByRole('button', { name: 'Sign in', exact: true }),
  connection: (p) => tid(p, 'connection'),
  healthBanner: (p) => tid(p, 'health-banner'),

  /* the main screen: avatar, conversation, what Jig's up to */
  chatView: (p) => tid(p, 'chat-view'),
  avatarRegion: (p) => p.getByRole('region', { name: 'Live avatar' }),
  avatar: (p) => ui.avatarRegion(p).locator('jig-avatar'),
  avatarSays: (p) => tid(p, 'avatar-says'),
  chatLog: (p) => tid(p, 'chat-log'),
  chatInput: (p) => tid(p, 'chat-input'),
  chatSend: (p) => tid(p, 'chat-send'),
  chatNew: (p) => tid(p, 'chat-new'),
  jigReplies: (p) => ui.chatLog(p).getByRole('article', { name: 'Jig replied' }),
  userMessages: (p) => ui.chatLog(p).getByRole('article', { name: 'You said' }),
  working: (scope) => scope.getByTestId('chat-working'),
  problem: (scope) => scope.getByTestId('chat-problem'),
  readOnlyNote: (p) => tid(p, 'read-only-note'),

  /* approval cards, inline in the conversation */
  approvalCard: (p, tool, status = 'pending') => ui.chatLog(p).locator(`[data-testid="approval"][data-tool="${tool}"][data-status="${status}"]`),
  approve: (scope, tool) => scope.getByRole('button', { name: `Yes, approve ${tool}`, exact: true }),
  deny: (scope, tool) => scope.getByRole('button', { name: `No, deny ${tool}`, exact: true }),
  approvalWill: (card) => card.getByTestId('approval-will'),
  approvalPreview: (card) => card.getByTestId('approval-preview'),
  approvalWhy: (card) => card.getByTestId('approval-why'),
  approvalWhySummary: (card) => ui.approvalWhy(card).locator('summary').filter({ hasText: 'Why am I asking?' }),
  approvalVerdict: (card) => card.getByTestId('sentinel-verdict'),
  approvalNote: (card) => card.getByTestId('approval-note'),
  approvalOutcome: (card) => card.getByTestId('approval-outcome'),

  /* what Jig's up to: the strip under the conversation and its drawer */
  doing: (p) => tid(p, 'doing'),
  doingText: (p) => tid(p, 'doing-text'),
  agentToggle: (p) => tid(p, 'agent-toggle'),
  doingOpen: (p) => tid(p, 'doing-open'),
  approvalsBadge: (p) => tid(p, 'approvals-badge'),
  activity: (p) => p.getByRole('dialog', { name: 'What Jig\'s up to' }),
  activityClose: (p) => tid(p, 'activity-close'),
  doingList: (p) => tid(p, 'doing-list'),
  doingItem: (p, title) => ui.doingList(p).getByTestId('doing-item').filter({ hasText: title }),
  pauseTask: (p, title) => ui.doingList(p).getByRole('button', { name: `Pause task ${title}`, exact: true }),
  resumeTask: (p, title) => ui.doingList(p).getByRole('button', { name: `Resume task ${title}`, exact: true }),
  goalDescription: (p) => ui.activity(p).getByLabel('What should Jig achieve?'),
  goalTitle: (p) => ui.activity(p).getByLabel('Title (optional)'),
  createGoal: (p) => tid(p, 'goal-create'),
  activityMore: (p) => tid(p, 'activity-more'),
  activityMoreSummary: (p) => ui.activityMore(p).locator('summary').filter({ hasText: 'Everything, in detail' }).first(),
  goalItem: (p, title) => tid(p, 'goals').getByTestId('goal').filter({ hasText: title }),
  goalResultDetails: (p, title) => ui.goalItem(p, title).locator(':scope > details.body'),
  goalResult: (p, title) => ui.goalResultDetails(p, title).locator('summary').filter({ hasText: /^Result$/ }),

  /* Settings */
  openSettings: (p) => tid(p, 'open-settings'),
  closeSettings: (p) => tid(p, 'close-settings'),
  settingsNav: (p, section) => tid(p, `settings-nav-${section}`),
  settingsSection: (p, section) => tid(p, `settings-${section}`),
  statusRegion: (p) => ui.settingsSection(p, 'model'),
  statusRefresh: (p) => tid(p, 'status-refresh'),
  statusTerm: (p, name) => ui.statusRegion(p).getByRole('term').filter({ hasText: name }),
  statusValue: (p, re) => ui.statusRegion(p).getByRole('definition').filter({ hasText: re }),
  autostartButton: (p) => ui.settingsSection(p, 'startup').getByRole('button', { name: /^Turn (on|off)/ }),
  autostartValue: (p) => ui.settingsSection(p, 'startup').getByRole('definition').filter({ has: p.getByRole('button', { name: /^Turn (on|off)/ }) }),

  /* Settings: what Jig can do on its own */
  toolChoice: (p, label) => ui.settingsSection(p, 'rules').getByRole('combobox', { name: label, exact: true }),
  toolChoiceRow: (p, tool) => ui.settingsSection(p, 'rules').locator(`[data-testid="tool-choice-row"][data-tool="${tool}"]`),
  toolChoiceSaved: (p) => tid(p, 'tool-choice-saved'),

  /* Settings: what Jig remembers about you */
  memoryList: (p) => tid(p, 'memories'),
  memoryItems: (p) => ui.memoryList(p).getByTestId('memory'),
  memorySearch: (p) => ui.settingsSection(p, 'memory').getByRole('searchbox', { name: 'Search words' }),
  memorySearchButton: (p) => tid(p, 'memory-search'),
  memoryShowAll: (p) => tid(p, 'memory-clear'),
  memoryEdit: (p, id) => ui.memoryList(p).getByRole('button', { name: `Edit memory ${id}`, exact: true }),
  memoryForget: (p, id) => ui.memoryList(p).getByRole('button', { name: `Forget memory ${id}`, exact: true }),
  memoryEditContent: (p, id) => ui.memoryList(p).getByLabel(`Edit memory #${id}`),
  memorySave: (p) => ui.memoryList(p).getByRole('button', { name: 'Save', exact: true }),
  memoryHeading: (p) => ui.settingsSection(p, 'memory').getByRole('heading', { name: /^Memories/ }),

  /* Settings: history and the audit log */
  auditKind: (p) => tid(p, 'audit-kind'),
  auditFilter: (p) => tid(p, 'audit-filter'),
  auditList: (p) => tid(p, 'audit'),
  auditRow: (p, kind) => ui.auditList(p).locator('summary').filter({ hasText: kind }),
  answeredList: (p) => tid(p, 'approvals-done'),

  /* Settings: in the conversation */
  readOnlySwitch: (p) => ui.settingsSection(p, 'chat').getByRole('switch', { name: 'Just look, don\'t touch' }),
  showWorkingSwitch: (p) => ui.settingsSection(p, 'chat').getByRole('switch', { name: 'Show Jig\'s working' }),
};

/** The avatar's live state, read from the real <jig-avatar> element's public getters. */
export async function avatarState(page) {
  return ui.avatar(page).evaluate((el) => ({ state: el.state, task: el.task, background: el.background }));
}
