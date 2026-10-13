export type LookupMode = 'application' | 'uniqueness'

export type BrowserState =
  | 'EDGE_NOT_RUNNING'
  | 'WAITING_SITE'
  | 'LOGIN_REQUIRED'
  | 'RETURNED_HOME'
  | 'READY'
  | 'RESULTS'
  | 'DETAIL'
  | 'CAPTCHA_REQUIRED'
  | 'AUTH_BLOCKED'
  | 'WAF_BLOCKED'

export type TaskState =
  | 'draft'
  | 'waiting_browser'
  | 'ready'
  | 'running'
  | 'pausing'
  | 'paused_user_action'
  | 'completed'
  | 'failed'
  | 'cancelled'
  | 'cancelling'

export type RowPhase = 'pending' | 'retry_pending' | 'running' | 'paused' | 'success' | 'failed'

export type RowStage =
  | 'idle'
  | 'searching'
  | 'reading_page'
  | 'checking_results'
  | 'opening_detail'
  | 'capturing_evidence'
  | 'writing_workbook'
  | 'paused'
  | 'completed'

export interface TaskRow {
  row: number
  mark: string
  applicant: string
  phase: RowPhase
  result: string
}

export interface SelectedFile {
  path: string
  name: string
  size: number
  mode: LookupMode
  templateName: string
  taskCount: number
}

export interface UiState {
  workerReady: boolean
  file: SelectedFile | null
  browserState: BrowserState
  taskState: TaskState
  rows: TaskRow[]
  alert: string | null
  outputPath: string | null
  runId: string | null
  progress: TaskProgress
}

export interface TaskProgress {
  current: number
  total: number
  currentMark: string
  stage: RowStage
  passNumber?: number
  page?: number
  pages?: number
}

export interface WorkerEvent {
  type: string
  [key: string]: unknown
}

export interface TaskCounts {
  total: number
  completed: number
  running: number
  pending: number
  success: number
  failed: number
}
