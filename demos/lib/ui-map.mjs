/*
 * Every element the demos touch, located only by accessible role and name, label text or data-testid.
 * No CSS classes, ids used for styling, or layout positions, so a restyle of the UI does not break the
 * recordings. If the redesign renames something, change it here (and nowhere else).
 *
 * Each entry is a function of the Playwright page (or a scope) returning one locator. There are no
 * alternatives or fallbacks: if an element cannot be found, the run fails and says which one.
 */

const exact = { exact: true };

export const ui = {
  /* sign-in */
  loginDialog: (p) => p.getByRole('dialog', { name: 'Sign in to Jig' }),
  tokenField: (p) => ui.loginDialog(p).getByLabel('API token', exact),
  signIn: (p) => ui.loginDialog(p).getByRole('button', { name: 'Sign in', exact: true }),
  connection: (p) => p.getByRole('status').filter({ hasText: /Live|Connecting|Signed out|Not connected|Disconnected|Unreachable/ }),

  /* side panel */
  avatarRegion: (p) => p.getByRole('region', { name: 'Live avatar' }),
  avatar: (p) => ui.avatarRegion(p).locator('jig-avatar'),
  statusRegion: (p) => p.getByRole('region', { name: 'Status' }),
  statusRefresh: (p) => ui.statusRegion(p).getByRole('button', { name: 'Refresh', exact: true }),
  statusTerm: (p, name) => ui.statusRegion(p).getByRole('term').filter({ hasText: name }),
  statusValue: (p, re) => ui.statusRegion(p).getByRole('definition').filter({ hasText: re }),
  autostartButton: (p) => ui.statusRegion(p).getByRole('button', { name: /^Turn (on|off)/ }),
  autostartValue: (p) => ui.statusRegion(p).getByRole('definition').filter({ has: p.getByRole('button', { name: /^Turn (on|off)/ }) }),

  /* tabs */
  tab: (p, name) => p.getByRole('tab', { name: new RegExp(`^${name}\\b`) }),
  panel: (p, name) => p.getByRole('tabpanel', { name: new RegExp(`^${name}\\b`) }),

  /* chat */
  chatInput: (p) => ui.panel(p, 'Chat').getByRole('textbox', { name: 'Message' }),
  chatMode: (p) => ui.panel(p, 'Chat').getByRole('combobox', { name: 'Mode' }),
  chatSend: (p) => ui.panel(p, 'Chat').getByRole('button', { name: 'Send', exact: true }),
  chatNew: (p) => ui.panel(p, 'Chat').getByRole('button', { name: 'New conversation', exact: true }),
  jigReplies: (p) => ui.panel(p, 'Chat').getByRole('article', { name: 'Jig replied' }),
  userMessages: (p) => ui.panel(p, 'Chat').getByRole('article', { name: 'You said' }),
  reviewButton: (scope) => scope.getByRole('button', { name: 'Review', exact: true }),

  /* activity */
  goalDescription: (p) => ui.panel(p, 'Activity').getByLabel('What should Jig achieve?'),
  goalTitle: (p) => ui.panel(p, 'Activity').getByLabel('Title (optional)'),
  createGoal: (p) => ui.panel(p, 'Activity').getByRole('button', { name: 'Create goal', exact: true }),
  activityRefresh: (p) => ui.panel(p, 'Activity').getByRole('button', { name: 'Refresh', exact: true }),
  runsTable: (p) => ui.panel(p, 'Activity').getByRole('table', { name: 'Recent runs' }),
  goalHeading: (p) => ui.panel(p, 'Activity').getByRole('heading', { name: 'Goals', exact: true }),

  /* approvals */
  approve: (p, tool) => ui.panel(p, 'Approvals').getByRole('button', { name: `Approve ${tool}`, exact: true }),
  deny: (p, tool) => ui.panel(p, 'Approvals').getByRole('button', { name: `Deny ${tool}`, exact: true }),
  waitingHeading: (p) => ui.panel(p, 'Approvals').getByRole('heading', { name: 'Waiting for you' }),
  /* memory */
  memorySearch: (p) => ui.panel(p, 'Memory').getByRole('searchbox', { name: 'Search words' }),
  memorySearchButton: (p) => ui.panel(p, 'Memory').getByRole('button', { name: 'Search', exact: true }),
  memoryShowAll: (p) => ui.panel(p, 'Memory').getByRole('button', { name: 'Show all', exact: true }),
  memoryEdit: (p, id) => ui.panel(p, 'Memory').getByRole('button', { name: `Edit memory ${id}`, exact: true }),
  memoryForget: (p, id) => ui.panel(p, 'Memory').getByRole('button', { name: `Forget memory ${id}`, exact: true }),
  memoryEditContent: (p, id) => ui.panel(p, 'Memory').getByLabel(`Edit memory #${id}`),
  memorySave: (p) => ui.panel(p, 'Memory').getByRole('button', { name: 'Save', exact: true }),
  memoryHeading: (p) => ui.panel(p, 'Memory').getByRole('heading', { name: /^Memories/ }),

  /* rules */
  ruleTool: (p) => ui.panel(p, 'Rules').getByLabel('Tool (glob)'),
  /* the select sits inside its label, so its accessible name also carries the selected option ("Decision Ask") */
  ruleDecision: (p) => ui.panel(p, 'Rules').getByRole('combobox', { name: /^Decision(?! for rule)/ }),
  ruleArg: (p) => ui.panel(p, 'Rules').getByLabel('Argument (optional)'),
  rulePattern: (p) => ui.panel(p, 'Rules').getByLabel('Pattern (optional)'),
  ruleNote: (p) => ui.panel(p, 'Rules').getByLabel('Note', exact),
  addRule: (p) => ui.panel(p, 'Rules').getByRole('button', { name: 'Add rule', exact: true }),
  rulesTable: (p) => ui.panel(p, 'Rules').getByRole('table', { name: 'Custom rules' }),
  ruleRowDecision: (p, tool) => ui.panel(p, 'Rules').getByRole('combobox', { name: `Decision for rule ${tool}`, exact: true }),
  ruleRowSave: (p, tool) => ui.panel(p, 'Rules').getByRole('button', { name: `Save rule ${tool}`, exact: true }),
  coreRulesHeading: (p) => ui.panel(p, 'Rules').getByRole('heading', { name: /^Core rules/ }),

  /* audit */
  auditKind: (p) => ui.panel(p, 'Audit log').getByLabel('Kind', exact),
  auditFilter: (p) => ui.panel(p, 'Audit log').getByRole('button', { name: 'Filter', exact: true }),
  auditHeading: (p) => ui.panel(p, 'Audit log').getByRole('heading', { name: 'Audit log', exact: true }),
};

/** The avatar's live state, read from the real <jig-avatar> element's public getters. */
export async function avatarState(page) {
  return ui.avatar(page).evaluate((el) => ({ state: el.state, task: el.task, background: el.background }));
}
