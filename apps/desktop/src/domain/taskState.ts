import type {
  BrowserState,
  LookupMode,
  RowPhase,
  RowStage,
  TaskCounts,
  TaskRow,
  TaskState,
  UiState,
  WorkerEvent,
} from './types'


export function initialUiState(): UiState {
  return {
    workerReady: false,
    file: null,
    browserState: 'EDGE_NOT_RUNNING',
    taskState: 'draft',
    rows: [],
    alert: null,
    outputPath: null,
    runId: null,
    progress: { current: 0, total: 0, currentMark: '', stage: 'idle' },
  }
}

export function reduceWorkerEvent(state: UiState, event: WorkerEvent): UiState {
  switch (event.type) {
    case 'hello':
      return { ...state, workerReady: true }
    case 'file_inspected':
      return fileInspected(state, event)
    case 'browser_state':
      return browserChanged(state, event)
    case 'task_state':
      return {
        ...state,
        taskState: asTaskState(event.state, state.taskState),
        runId: text(event.run_id) ?? state.runId,
        outputPath: text(event.output_path) ?? state.outputPath,
        progress: {
          ...state.progress,
          current: positiveNumber(event.current) || state.progress.current,
          total: positiveNumber(event.total) || state.progress.total,
        },
      }
    case 'row_progress':
      return rowProgress(state, event)
    case 'row_deferred':
      return rowDeferred(state, event)
    case 'paused':
      return paused(state, event)
    case 'completed':
      return {
        ...state,
        taskState: 'completed',
        outputPath: text(event.output_path) ?? state.outputPath,
        runId: text(event.run_id) ?? state.runId,
        alert: null,
        progress: {
          ...state.progress,
          current: positiveNumber(event.current) || state.progress.total,
          total: positiveNumber(event.total) || state.progress.total,
          stage: 'completed',
        },
      }
    case 'error':
      return {
        ...state,
        taskState: state.taskState === 'running' ? 'failed' : state.taskState,
        alert: text(event.message) ?? '发生未知错误',
      }
    default:
      return state
  }
}

export function taskCounts(rows: TaskRow[]): TaskCounts {
  const success = rows.filter((row) => row.phase === 'success').length
  const failed = rows.filter((row) => row.phase === 'failed').length
  const running = rows.filter((row) => row.phase === 'running').length
  const pending = rows.filter((row) => ['pending', 'retry_pending', 'paused'].includes(row.phase)).length
  return {
    total: rows.length,
    completed: success + failed,
    running,
    pending,
    success,
    failed,
  }
}

export function pauseMessage(reason: string): string {
  const messages: Record<string, string> = {
    USER_REQUESTED: '任务已由用户暂停，点击“继续”即可恢复后台查询。',
    LOGIN_REQUIRED: '商标网站登录已失效。已保存阶段性结果，请在专用 Edge 中重新登录。',
    AUTH_BLOCKED: '商标网站登录已失效。已保存阶段性结果，请从网站首页重新登录。',
    CAPTCHA_REQUIRED: '商标网站要求人工验证。已保存阶段性结果，请在专用 Edge 中完成验证。',
  }
  return messages[reason] ?? '当前商标暂时无法完成，程序会继续处理并在末尾自动重试。'
}

function fileInspected(state: UiState, event: WorkerEvent): UiState {
  const path = text(event.path) ?? ''
  const mode = event.mode === 'uniqueness' ? 'uniqueness' : 'application'
  const rows = Array.isArray(event.rows)
    ? event.rows.flatMap((item) => {
        if (!isRecord(item) || typeof item.row !== 'number' || typeof item.mark !== 'string') {
          return []
        }
        return [{
          row: item.row,
          mark: item.mark,
          applicant: text(item.applicant) ?? '',
          phase: 'pending' as RowPhase,
          result: '-',
        }]
      })
    : []
  return {
    ...state,
    browserState: state.browserState === 'EDGE_NOT_RUNNING' ? 'EDGE_NOT_RUNNING' : 'WAITING_SITE',
    file: {
      path,
      name: fileName(path),
      size: number(event.size),
      mode,
      templateName: templateName(mode),
      taskCount: number(event.task_count),
    },
    rows,
    runId: null,
    outputPath: null,
    alert: null,
    taskState: 'waiting_browser',
    progress: {
      current: 0,
      total: number(event.task_count) || rows.length,
      currentMark: '',
      stage: 'idle',
    },
  }
}

function browserChanged(state: UiState, event: WorkerEvent): UiState {
  const browserState = asBrowserState(event.state)
  const isReady = browserState === 'READY'
  const taskState =
    state.taskState === 'running' || state.taskState === 'paused_user_action'
      ? state.taskState
      : isReady && state.file
        ? 'ready'
        : state.file
          ? 'waiting_browser'
          : state.taskState
  return {
    ...state,
    browserState,
    taskState,
    alert: isReady && state.taskState !== 'paused_user_action' ? null : state.alert,
  }
}

function rowProgress(state: UiState, event: WorkerEvent): UiState {
  const rowNumber = number(event.row)
  const existing = state.rows.find((row) => row.row === rowNumber)
  const isActive = event.phase === 'started' || event.phase === 'stage'
  const phase = isActive
    ? 'running'
    : event.status === 'success'
      ? 'success'
      : 'failed'
  const updated: TaskRow = {
    row: rowNumber,
    mark: text(event.mark) ?? existing?.mark ?? '',
    applicant: text(event.applicant) ?? existing?.applicant ?? '',
    phase,
    result: resultText(event, phase),
  }
  return {
    ...state,
    rows: replaceRow(state.rows, updated),
    progress: {
      current: positiveNumber(event.current) || state.progress.current,
      total: positiveNumber(event.total) || state.progress.total,
      currentMark: updated.mark || state.progress.currentMark,
      stage: event.phase === 'stage'
        ? asRowStage(event.stage, state.progress.stage)
        : phase === 'running'
          ? state.progress.stage
          : phase === 'success' || phase === 'failed'
            ? 'completed'
            : state.progress.stage,
      ...(positiveNumber(event.pass_number) ? { passNumber: positiveNumber(event.pass_number) } : {}),
      ...(positiveNumber(event.page) ? { page: positiveNumber(event.page) } : {}),
      ...(positiveNumber(event.pages) ? { pages: positiveNumber(event.pages) } : {}),
    },
  }
}

function rowDeferred(state: UiState, event: WorkerEvent): UiState {
  const rowNumber = number(event.row)
  const existing = state.rows.find((row) => row.row === rowNumber)
  const updated: TaskRow = {
    row: rowNumber,
    mark: text(event.mark) ?? existing?.mark ?? '',
    applicant: text(event.applicant) ?? existing?.applicant ?? '',
    phase: 'retry_pending',
    result: `首轮暂未完成，稍后自动重试：${text(event.message) ?? '页面暂时不可用'}`,
  }
  return {
    ...state,
    taskState: 'running',
    alert: null,
    rows: replaceRow(state.rows, updated),
    progress: {
      ...state.progress,
      current: positiveNumber(event.current) || state.progress.current,
      total: positiveNumber(event.total) || state.progress.total,
      currentMark: updated.mark,
      stage: 'completed',
    },
  }
}

function paused(state: UiState, event: WorkerEvent): UiState {
  const rowNumber = number(event.row)
  const reason = text(event.reason) ?? ''
  return {
    ...state,
    browserState: isBrowserState(reason) ? reason : state.browserState,
    taskState: 'paused_user_action',
    alert: pauseMessage(reason),
    outputPath: text(event.output_path) ?? state.outputPath,
    progress: {
      ...state.progress,
      current: positiveNumber(event.current) || state.progress.current,
      total: positiveNumber(event.total) || state.progress.total,
      stage: 'paused',
    },
    rows: state.rows.map((row) =>
      row.row === rowNumber || row.phase === 'running' ? { ...row, phase: 'paused' } : row,
    ),
  }
}

function replaceRow(rows: TaskRow[], updated: TaskRow): TaskRow[] {
  const index = rows.findIndex((row) => row.row === updated.row)
  if (index < 0) return [...rows, updated]
  return rows.map((row) => (row.row === updated.row ? updated : row))
}

function resultText(event: WorkerEvent, phase: RowPhase): string {
  if (phase === 'running') return stageLabel(event.stage)
  return (
    text(event.term) ??
    text(event.uniqueness) ??
    text(event.message) ??
    (phase === 'success' ? '查询成功' : '未找到有效结果')
  )
}

export function stageLabel(value: unknown): string {
  const labels: Record<RowStage, string> = {
    idle: '准备查询',
    searching: '正在检索并读取结果页',
    reading_page: '正在读取并识别查询结果页',
    checking_results: '正在核对查询结果',
    opening_detail: '正在打开并核对商标详情',
    capturing_evidence: '正在生成证据截图',
    writing_workbook: '正在写入结果表格',
    paused: '等待人工处理后继续',
    completed: '当前商标处理完成',
  }
  return labels[asRowStage(value, 'searching')]
}

function templateName(mode: LookupMode): string {
  return mode === 'application' ? '商标申请查询' : '商标唯一性查询'
}

function fileName(path: string): string {
  return path.split(/[\\/]/).pop() || path
}

function text(value: unknown): string | null {
  return typeof value === 'string' && value.trim() ? value.trim() : null
}

function number(value: unknown): number {
  return typeof value === 'number' && Number.isFinite(value) ? value : 0
}

function positiveNumber(value: unknown): number {
  const parsed = number(value)
  return parsed > 0 ? parsed : 0
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null
}

function asBrowserState(value: unknown): BrowserState {
  return typeof value === 'string' ? (value as BrowserState) : 'WAITING_SITE'
}

function isBrowserState(value: string): value is BrowserState {
  return [
    'EDGE_NOT_RUNNING',
    'WAITING_SITE',
    'LOGIN_REQUIRED',
    'RETURNED_HOME',
    'READY',
    'RESULTS',
    'DETAIL',
    'CAPTCHA_REQUIRED',
    'AUTH_BLOCKED',
    'WAF_BLOCKED',
  ].includes(value)
}

function asTaskState(value: unknown, fallback: TaskState): TaskState {
  return typeof value === 'string' ? (value as TaskState) : fallback
}

function asRowStage(value: unknown, fallback: RowStage): RowStage {
  const stages: RowStage[] = [
    'idle',
    'searching',
    'reading_page',
    'checking_results',
    'opening_detail',
    'capturing_evidence',
    'writing_workbook',
    'paused',
    'completed',
  ]
  return typeof value === 'string' && stages.includes(value as RowStage)
    ? value as RowStage
    : fallback
}
