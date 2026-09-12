export const messages = {
  en: {
    'view.plugins.title': 'Plugin Manager',
    'view.plugins.sub': 'Install, configure and manage runtime plugins.',

    // ── Tabs ──────────────────────────────────────────────────────
    'view.plugins.tab.list': 'Plugins',
    'view.plugins.tab.discover': 'Discover',

    // ── Plugin list ───────────────────────────────────────────────
    'view.plugins.list.title': 'Installed Plugins',
    'view.plugins.list.empty': 'No plugins installed',
    'view.plugins.list.emptyHint': 'Run discover to scan for available plugins.',
    'view.plugins.list.failedLoad': 'Failed to load plugins',
    'view.plugins.list.discoverBtn': 'Discover',
    'view.plugins.list.loadAll': 'Load All',
    'view.plugins.list.startAll': 'Start All',
    'view.plugins.list.stopAll': 'Stop All',

    // ── Discover ──────────────────────────────────────────────────
    'view.plugins.discover.title': 'Discovered Plugins',
    'view.plugins.discover.empty': 'No plugins discovered',
    'view.plugins.discover.emptyHint': 'Place plugin packages under the plugins directory and run discover.',
    'view.plugins.discover.failedLoad': 'Failed to discover plugins',
    'view.plugins.discover.scanBtn': 'Scan Now',
    'view.plugins.discover.scanning': 'Scanning…',

    // ── Columns ───────────────────────────────────────────────────
    'view.plugins.col.id': 'ID',
    'view.plugins.col.version': 'Version',
    'view.plugins.col.state': 'State',
    'view.plugins.col.actions': 'Actions',

    // ── States ────────────────────────────────────────────────────
    'view.plugins.state.loaded': 'Loaded',
    'view.plugins.state.started': 'Running',
    'view.plugins.state.stopped': 'Stopped',
    'view.plugins.state.unloaded': 'Unloaded',
    'view.plugins.state.error': 'Error',
    'view.plugins.state.unknown': 'Unknown',

    // ── Actions ───────────────────────────────────────────────────
    'view.plugins.action.load': 'Load',
    'view.plugins.action.start': 'Start',
    'view.plugins.action.stop': 'Stop',
    'view.plugins.action.reload': 'Reload',
    'view.plugins.action.config': 'Configure',
    'view.plugins.action.loading': 'Working…',

    // ── Config modal ──────────────────────────────────────────────
    'view.plugins.modal.configTitle': 'Plugin Configuration',
    'view.plugins.modal.configHint': 'Edit JSON configuration for this plugin.',
    'view.plugins.modal.configInvalid': 'Invalid JSON',
    'view.plugins.modal.saving': 'Saving…',
    'view.plugins.modal.saveFailed': 'Failed to save configuration',

    // ── Toast / status ────────────────────────────────────────────
    'view.plugins.toast.loaded': 'Plugin loaded',
    'view.plugins.toast.started': 'Plugin started',
    'view.plugins.toast.stopped': 'Plugin stopped',
    'view.plugins.toast.reloaded': 'Plugin reloaded',
    'view.plugins.toast.configSaved': 'Configuration saved',
    'view.plugins.toast.loadFailed': 'Failed to load plugin',
    'view.plugins.toast.startFailed': 'Failed to start plugin',
    'view.plugins.toast.stopFailed': 'Failed to stop plugin',
    'view.plugins.toast.reloadFailed': 'Failed to reload plugin',
    'view.plugins.toast.discoverOk': 'Discovered {n} plugin(s)',
    'view.plugins.toast.discoverFailed': 'Discovery failed',
    'view.plugins.toast.loadAllOk': 'Loaded all plugins',
    'view.plugins.toast.loadAllFailed': 'Failed to load all plugins',
    'view.plugins.toast.startAllOk': 'Started all plugins',
    'view.plugins.toast.startAllFailed': 'Failed to start all plugins',
    'view.plugins.toast.stopAllOk': 'Stopped all plugins',
    'view.plugins.toast.stopAllFailed': 'Failed to stop all plugins',

    // ── Filter ────────────────────────────────────────────────────
    'view.plugins.filter.allStates': 'All states',
  },

  zh: {
    'view.plugins.title': '插件管理',
    'view.plugins.sub': '安装、配置和管理运行时插件。',

    // ── 标签页 ────────────────────────────────────────────────────
    'view.plugins.tab.list': '插件列表',
    'view.plugins.tab.discover': '发现',

    // ── 插件列表 ──────────────────────────────────────────────────
    'view.plugins.list.title': '已安装插件',
    'view.plugins.list.empty': '暂无已安装插件',
    'view.plugins.list.emptyHint': '运行发现以扫描可用插件。',
    'view.plugins.list.failedLoad': '加载插件失败',
    'view.plugins.list.discoverBtn': '发现',
    'view.plugins.list.loadAll': '全部加载',
    'view.plugins.list.startAll': '全部启动',
    'view.plugins.list.stopAll': '全部停止',

    // ── 发现 ──────────────────────────────────────────────────────
    'view.plugins.discover.title': '已发现插件',
    'view.plugins.discover.empty': '未发现插件',
    'view.plugins.discover.emptyHint': '将插件包放入插件目录后运行发现。',
    'view.plugins.discover.failedLoad': '发现插件失败',
    'view.plugins.discover.scanBtn': '立即扫描',
    'view.plugins.discover.scanning': '扫描中…',

    // ── 列 ────────────────────────────────────────────────────────
    'view.plugins.col.id': '标识',
    'view.plugins.col.version': '版本',
    'view.plugins.col.state': '状态',
    'view.plugins.col.actions': '操作',

    // ── 状态 ──────────────────────────────────────────────────────
    'view.plugins.state.loaded': '已加载',
    'view.plugins.state.started': '运行中',
    'view.plugins.state.stopped': '已停止',
    'view.plugins.state.unloaded': '未加载',
    'view.plugins.state.error': '错误',
    'view.plugins.state.unknown': '未知',

    // ── 操作 ──────────────────────────────────────────────────────
    'view.plugins.action.load': '加载',
    'view.plugins.action.start': '启动',
    'view.plugins.action.stop': '停止',
    'view.plugins.action.reload': '重载',
    'view.plugins.action.config': '配置',
    'view.plugins.action.loading': '处理中…',

    // ── 配置弹窗 ──────────────────────────────────────────────────
    'view.plugins.modal.configTitle': '插件配置',
    'view.plugins.modal.configHint': '编辑此插件的 JSON 配置。',
    'view.plugins.modal.configInvalid': 'JSON 无效',
    'view.plugins.modal.saving': '保存中…',
    'view.plugins.modal.saveFailed': '保存配置失败',

    // ── 提示 / 状态 ───────────────────────────────────────────────
    'view.plugins.toast.loaded': '插件已加载',
    'view.plugins.toast.started': '插件已启动',
    'view.plugins.toast.stopped': '插件已停止',
    'view.plugins.toast.reloaded': '插件已重载',
    'view.plugins.toast.configSaved': '配置已保存',
    'view.plugins.toast.loadFailed': '加载插件失败',
    'view.plugins.toast.startFailed': '启动插件失败',
    'view.plugins.toast.stopFailed': '停止插件失败',
    'view.plugins.toast.reloadFailed': '重载插件失败',
    'view.plugins.toast.discoverOk': '发现 {n} 个插件',
    'view.plugins.toast.discoverFailed': '发现失败',
    'view.plugins.toast.loadAllOk': '已加载全部插件',
    'view.plugins.toast.loadAllFailed': '加载全部插件失败',
    'view.plugins.toast.startAllOk': '已启动全部插件',
    'view.plugins.toast.startAllFailed': '启动全部插件失败',
    'view.plugins.toast.stopAllOk': '已停止全部插件',
    'view.plugins.toast.stopAllFailed': '停止全部插件失败',

    // ── 过滤 ──────────────────────────────────────────────────────
    'view.plugins.filter.allStates': '全部状态',
  },
};