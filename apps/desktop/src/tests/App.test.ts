import { flushPromises, mount } from '@vue/test-utils'
import { afterEach, describe, expect, it, vi } from 'vitest'

import App from '../App.vue'
import { DESKTOP_SERVICE_KEY, type DesktopService } from '../services/desktop'
import type { WorkerEvent } from '../domain/types'


class FakeDesktopService implements DesktopService {
  listener: ((event: WorkerEvent) => void) | null = null
  sent: Record<string, unknown>[] = []
  notifications: Array<{ title: string; body: string }> = []
  autoBrowserStatus = true
  autoConnectStatus = true
  minimizeCalls = 0
  templateDownloads: string[] = []
  fileDropHandler: ((paths: string[]) => void) | null = null
  settings = {
    DASHSCOPE_BASE_URL: 'https://gateway.example.com/v1',
    DASHSCOPE_API_KEY: 'fake-company-key',
    DASHSCOPE_MODEL: 'qwen-vl-max',
  }
  defaults = {
    DASHSCOPE_BASE_URL: 'https://dashscope.aliyuncs.com/compatible-mode/v1',
    DASHSCOPE_API_KEY: 'fake-default-key',
    DASHSCOPE_MODEL: 'qwen3.7-plus',
  }

  async startWorker(): Promise<void> {
    this.listener?.({ type: 'hello', protocol: 1 })
  }

  async stopWorker(): Promise<void> {}

  async listen(handler: (event: WorkerEvent) => void): Promise<() => void> {
    this.listener = handler
    return () => {
      this.listener = null
    }
  }

  async pickXlsx(): Promise<string | null> {
    return 'D:/资料/商标申请.xlsx'
  }

  async listenFileDrop(handler: (paths: string[]) => void): Promise<() => void> {
    this.fileDropHandler = handler
    return () => {
      this.fileDropHandler = null
    }
  }

  async downloadTemplate(kind: 'application' | 'uniqueness'): Promise<string | null> {
    this.templateDownloads.push(kind)
    return `D:/模板/${kind}.xlsx`
  }

  async send(command: Record<string, unknown>): Promise<void> {
    this.sent.push(command)
    if (command.type === 'inspect_file') {
      this.listener?.({
        type: 'file_inspected',
        path: command.path,
        mode: 'application',
        task_count: 1,
        size: 10_240,
        rows: [{ row: 2, mark: '云杉月付', applicant: '北京云杉网络技术有限公司' }],
      })
    }
    if (command.type === 'open_browser') {
      this.listener?.({ type: 'browser_state', state: 'LOGIN_REQUIRED' })
    }
    if ((command.type === 'connect_browser' && this.autoConnectStatus)
      || (command.type === 'browser_status' && this.autoBrowserStatus)) {
      this.listener?.({ type: 'browser_state', state: 'READY' })
    }
  }

  async saveOutputAs(): Promise<string | null> {
    return 'D:/结果/商标申请_已查询.xlsx'
  }

  async loadModelSettings() {
    return { current: { ...this.settings }, defaults: { ...this.defaults } }
  }

  async saveModelSettings(settings: typeof this.settings) {
    this.settings = { ...settings }
    return { ...this.settings }
  }

  async notify(title: string, body: string): Promise<void> {
    this.notifications.push({ title, body })
  }

  async minimizeApp(): Promise<void> {
    this.minimizeCalls += 1
  }

  emit(event: WorkerEvent): void {
    this.listener?.(event)
  }

  emitFileDrop(paths: string[]): void {
    this.fileDropHandler?.(paths)
  }
}


function mountApp(service: FakeDesktopService) {
  return mount(App, {
    global: {
      provide: {
        [DESKTOP_SERVICE_KEY as symbol]: service,
      },
    },
  })
}


describe('App', () => {
  it('downloads both workbook templates from the step-one content area', async () => {
    const service = new FakeDesktopService()
    const wrapper = mountApp(service)
    await flushPromises()

    await wrapper.get('[data-testid="download-application-template"]').trigger('click')
    await wrapper.get('[data-testid="download-uniqueness-template"]').trigger('click')
    await flushPromises()

    expect(service.templateDownloads).toEqual(['application', 'uniqueness'])
  })

  it('inspects an XLSX workbook dropped into the desktop window', async () => {
    const service = new FakeDesktopService()
    const wrapper = mountApp(service)
    await flushPromises()

    service.emitFileDrop(['D:/资料/拖入的商标申请.xlsx'])
    await flushPromises()

    expect(service.sent).toContainEqual({
      type: 'inspect_file',
      protocol: 1,
      path: 'D:/资料/拖入的商标申请.xlsx',
    })
    expect(wrapper.text()).toContain('拖入的商标申请.xlsx')
  })

  it('rechecks login before allowing a newly selected workbook to start', async () => {
    const service = new FakeDesktopService()
    const wrapper = mountApp(service)
    await flushPromises()
    await wrapper.get('[data-testid="select-file"]').trigger('click')
    service.emit({ type: 'browser_state', state: 'READY' })
    service.emit({ type: 'completed', run_id: 'first-run', output_path: 'D:/first-output.xlsx' })
    await flushPromises()
    service.autoBrowserStatus = false

    await wrapper.get('[data-testid="select-file"]').trigger('click')
    await flushPromises()

    expect(service.sent.at(-1)?.type).toBe('browser_status')
    expect(wrapper.get('[data-testid="start-task"]').attributes('disabled')).toBeDefined()
    service.emit({ type: 'browser_state', state: 'READY' })
    await flushPromises()
    expect(wrapper.get('[data-testid="start-task"]').attributes('disabled')).toBeUndefined()
  })
  afterEach(() => {
    vi.useRealTimers()
  })

  it('专用 Edge 登录后检查状态并启用开始按钮', async () => {
    const service = new FakeDesktopService()
    const wrapper = mountApp(service)
    await flushPromises()

    await wrapper.get('[data-testid="select-file"]').trigger('click')
    await wrapper.get('[data-testid="open-browser"]').trigger('click')
    await flushPromises()

    expect(wrapper.text()).toContain('商标申请查询')
    expect(wrapper.text()).toContain('云杉月付')
    expect(wrapper.text()).toContain('请在专用 Edge 中自行完成登录')
    expect(wrapper.get('[data-testid="start-task"]').attributes('disabled')).toBeDefined()
    expect(service.minimizeCalls).toBe(0)

    await wrapper.get('[data-testid="connect-browser"]').trigger('click')
    await flushPromises()

    expect(wrapper.get('[data-testid="start-task"]').attributes('disabled')).toBeUndefined()
    expect(service.sent.map((command) => command.type)).toContain('connect_browser')
  })

  it('点击已登录后立即显示正在检查，避免 Worker 检测期间界面看起来没有反应', async () => {
    vi.useFakeTimers()
    const service = new FakeDesktopService()
    service.autoConnectStatus = false
    const wrapper = mountApp(service)
    await flushPromises()
    await wrapper.get('[data-testid="select-file"]').trigger('click')
    await wrapper.get('[data-testid="open-browser"]').trigger('click')
    await flushPromises()

    await wrapper.get('[data-testid="connect-browser"]').trigger('click')
    await flushPromises()

    expect(service.sent.at(-1)?.type).toBe('connect_browser')
    expect(wrapper.get('[data-testid="connect-browser"]').text()).toContain('正在检查')
    expect(wrapper.get('[data-testid="connect-browser"]').attributes('disabled')).toBeDefined()

    await vi.advanceTimersByTimeAsync(30_000)
    await flushPromises()

    expect(wrapper.text()).toContain('登录状态检查超时')
    expect(wrapper.get('[data-testid="connect-browser"]').text()).toContain('我已登录')
    expect(wrapper.get('[data-testid="connect-browser"]').attributes('disabled')).toBeUndefined()
    wrapper.unmount()
  })

  it('只有用户点击暂停才进入暂停状态且无需重新检测即可继续', async () => {
    const service = new FakeDesktopService()
    const wrapper = mountApp(service)
    await flushPromises()
    await wrapper.get('[data-testid="select-file"]').trigger('click')
    service.emit({ type: 'browser_state', state: 'READY' })
    service.emit({ type: 'task_state', state: 'running', run_id: 'r1' })
    service.emit({ type: 'row_progress', phase: 'started', row: 2, mark: '云杉月付' })
    await flushPromises()

    await wrapper.get('[data-testid="pause-task"]').trigger('click')
    expect(service.sent.at(-1)?.type).toBe('pause')

    service.emit({ type: 'task_state', state: 'pausing' })
    service.emit({ type: 'paused', reason: 'USER_REQUESTED', recoverable: true, row: 3 })
    await flushPromises()

    expect(wrapper.text()).toContain('任务已由用户暂停')
    expect(wrapper.find('[data-testid="recheck-browser"]').exists()).toBe(false)
    expect(wrapper.get('[data-testid="resume-task"]').attributes('disabled')).toBeUndefined()
  })

  it('暂停后取消会携带任务编号并保留阶段性结果供另存', async () => {
    const service = new FakeDesktopService()
    const wrapper = mountApp(service)
    await flushPromises()
    await wrapper.get('[data-testid="select-file"]').trigger('click')
    service.emit({ type: 'task_state', state: 'running', run_id: 'paused-run' })
    service.emit({
      type: 'paused',
      reason: 'USER_REQUESTED',
      recoverable: true,
      row: 3,
      current: 1,
      total: 2,
      output_path: 'D:/结果/阶段性结果.xlsx',
    })
    await flushPromises()

    await wrapper.get('[data-testid="cancel-task"]').trigger('click')
    await flushPromises()

    expect(service.sent.at(-1)).toMatchObject({
      type: 'cancel',
      protocol: 1,
      run_id: 'paused-run',
    })
    service.emit({
      type: 'task_state',
      state: 'cancelled',
      run_id: 'paused-run',
      current: 1,
      output_path: 'D:/结果/阶段性结果.xlsx',
    })
    await flushPromises()

    expect(wrapper.text()).toContain('已完成的商标已保存')
    expect(wrapper.get('[data-testid="save-as"]').attributes('disabled')).toBeUndefined()
  })

  it('单条异常进入末尾重试队列而不是暂停整批任务', async () => {
    const service = new FakeDesktopService()
    const wrapper = mountApp(service)
    await flushPromises()
    await wrapper.get('[data-testid="select-file"]').trigger('click')
    service.emit({ type: 'task_state', state: 'running', run_id: 'r2' })
    service.emit({
      type: 'row_deferred',
      row: 2,
      mark: '云杉月付',
      attempt: 1,
      will_retry: true,
      message: '结果页暂时不可用',
      current: 1,
      total: 1,
    })
    await flushPromises()

    expect(wrapper.text()).toContain('稍后自动重试')
    expect(wrapper.text()).not.toContain('任务已暂停')
    expect(wrapper.find('[data-testid="recheck-browser"]').exists()).toBe(false)
    expect(wrapper.get('[data-testid="pause-task"]').attributes('disabled')).toBeUndefined()
  })

  it('登录失效后立即暂停、禁用继续并发送系统通知', async () => {
    const service = new FakeDesktopService()
    const wrapper = mountApp(service)
    await flushPromises()
    await wrapper.get('[data-testid="select-file"]').trigger('click')
    service.emit({ type: 'task_state', state: 'running', run_id: 'overnight-run' })
    service.emit({ type: 'row_progress', phase: 'started', row: 2, mark: '夜间商标' })
    service.emit({
      type: 'paused',
      reason: 'LOGIN_REQUIRED',
      message: '商标网站要求重新登录',
      recoverable: true,
      row: 2,
      current: 0,
      total: 1,
      output_path: 'D:/结果/夜间任务_阶段性结果.xlsx',
    })
    await flushPromises()

    expect(wrapper.text()).toContain('商标网站登录已失效')
    expect(wrapper.get('[data-testid="resume-task"]').attributes('disabled')).toBeDefined()
    expect(wrapper.get('[data-testid="open-browser"]').attributes('disabled')).toBeUndefined()
    expect(service.notifications).toContainEqual({
      title: '商标查询已暂停',
      body: '商标网站要求重新登录；已保存阶段性结果，请完成登录后继续。',
    })
  })

  it('完成后启用另存为且显示商标申请人', async () => {
    const service = new FakeDesktopService()
    const wrapper = mountApp(service)
    await flushPromises()
    service.emit({
      type: 'file_inspected',
      path: 'D:/资料/商标申请.xlsx',
      mode: 'application',
      task_count: 1,
      size: 1024,
      rows: [{ row: 2, mark: '云杉月付', applicant: '北京云杉网络技术有限公司' }],
    })
    service.emit({ type: 'completed', output_path: 'D:/结果/商标申请_已查询.xlsx' })
    await flushPromises()

    expect(wrapper.findAll('thead th').map((cell) => cell.text())).toEqual([
      '行号',
      '商标',
      '商标申请人',
      '阶段',
      '结果',
    ])
    expect(wrapper.text()).toContain('北京云杉网络技术有限公司')
    expect(wrapper.get('[data-testid="save-as"]').attributes('disabled')).toBeUndefined()
  })

  it('后台任务完成后发送不激活窗口的系统通知', async () => {
    const service = new FakeDesktopService()
    const wrapper = mountApp(service)
    await flushPromises()
    service.emit({
      type: 'file_inspected',
      path: 'D:/资料/批量商标申请.xlsx',
      mode: 'application',
      task_count: 2,
      size: 2048,
      rows: [
        { row: 2, mark: '商标一' },
        { row: 3, mark: '商标二' },
      ],
    })
    service.emit({ type: 'row_progress', phase: 'completed', row: 2, mark: '商标一', status: 'success' })
    service.emit({ type: 'row_progress', phase: 'completed', row: 3, mark: '商标二', status: 'failed', message: '最终失败' })
    service.emit({ type: 'completed', output_path: 'D:/结果/批量商标申请_已查询.xlsx' })
    await flushPromises()

    expect(service.notifications).toEqual([
      { title: '商标查询已完成', body: '成功 1 项，失败 1 项；结果已保存在本机。' },
    ])
  })

  it('批量任务醒目显示当前商标进度阶段和运行时间', async () => {
    vi.useFakeTimers()
    const service = new FakeDesktopService()
    const wrapper = mountApp(service)
    await flushPromises()
    service.emit({
      type: 'file_inspected',
      path: 'D:/资料/批量商标申请.xlsx',
      mode: 'application',
      task_count: 12,
      size: 12_000,
      rows: Array.from({ length: 12 }, (_, index) => ({
        row: index + 2,
        mark: `商标${index + 1}`,
      })),
    })
    await flushPromises()
    expect(wrapper.get('[data-testid="task-progress"]').text()).toContain('尚未开始，共 12 个')
    service.emit({ type: 'task_state', state: 'running', run_id: 'batch', total: 12 })
    service.emit({
      type: 'row_progress',
      phase: 'started',
      row: 8,
      mark: '商标7',
      current: 7,
      total: 12,
    })
    service.emit({
      type: 'row_progress',
      phase: 'stage',
      row: 8,
      mark: '商标7',
      current: 7,
      total: 12,
      stage: 'capturing_evidence',
    })
    await vi.advanceTimersByTimeAsync(65_000)
    await flushPromises()

    const progress = wrapper.get('[data-testid="task-progress"]')
    expect(progress.text()).toContain('商标7')
    expect(progress.text()).toContain('第 7 个，共 12 个')
    expect(progress.text()).toContain('正在生成证据截图')
    expect(progress.text()).toContain('1分05秒')
    expect(progress.text()).toContain('完成首个商标后开始估算')
    expect(progress.get('[role="progressbar"]').attributes('aria-valuenow')).toBe('0')
    expect(wrapper.get('[data-testid="select-file"]').attributes('disabled')).toBeDefined()
    expect(wrapper.get('[aria-label="移除文件"]').attributes('disabled')).toBeDefined()
    const viewQuery = wrapper.get('[data-testid="open-browser"]')
    expect(viewQuery.attributes('disabled')).toBeUndefined()
    expect(viewQuery.text()).toContain('查看查询过程')
    await viewQuery.trigger('click')
    await flushPromises()
    expect(service.sent.at(-1)?.type).toBe('show_browser')
    expect(service.minimizeCalls).toBe(0)
    expect(wrapper.get('[data-row="8"]').classes()).toContain('is-active')
    wrapper.unmount()
  })

  it('暂停后打开专用 Edge 失败时保留助手窗口并显示错误', async () => {
    const service = new FakeDesktopService()
    const wrapper = mountApp(service)
    await flushPromises()
    await wrapper.get('[data-testid="select-file"]').trigger('click')
    service.emit({ type: 'browser_state', state: 'READY' })
    service.emit({ type: 'paused', reason: 'LOGIN_REQUIRED', row: 2, message: '请重新登录' })

    await wrapper.get('[data-testid="open-browser"]').trigger('click')
    await flushPromises()
    expect(service.sent.at(-1)?.type).toBe('show_browser')
    expect(service.minimizeCalls).toBe(0)
    expect(wrapper.get('[data-testid="open-browser"]').text()).toContain('正在打开')

    service.emit({ type: 'error', code: 'EDGE_WINDOW_UNAVAILABLE', message: '未能显示专用 Edge，请重新点击打开商标网站' })
    await flushPromises()
    expect(wrapper.get('[role="alert"]').text()).toContain('未能显示专用 Edge')
    expect(wrapper.get('[data-testid="open-browser"]').attributes('disabled')).toBeUndefined()
    wrapper.unmount()
  })

  it('多页重试时显示轮次和真实页码', async () => {
    const service = new FakeDesktopService()
    const wrapper = mountApp(service)
    await flushPromises()
    service.emit({
      type: 'file_inspected',
      path: 'D:/资料/商标查询是否唯一性.xlsx',
      mode: 'uniqueness',
      task_count: 1,
      size: 2048,
      rows: [{ row: 2, mark: '可复美' }],
    })
    service.emit({ type: 'task_state', state: 'running', run_id: 'pages' })
    service.emit({
      type: 'row_progress',
      phase: 'stage',
      stage: 'reading_page',
      row: 2,
      mark: '可复美',
      pass_number: 2,
      page: 3,
      pages: 5,
      current: 1,
      total: 1,
    })
    await flushPromises()

    const progress = wrapper.get('[data-testid="task-progress"]')
    expect(progress.text()).toContain('第 2 轮重试')
    expect(progress.text()).toContain('第 3 / 5 页')
  })

  it('allows non-technical users to review, replace, and restore model settings', async () => {
    vi.useFakeTimers()
    const service = new FakeDesktopService()
    const wrapper = mountApp(service)
    await flushPromises()

    await wrapper.get('[data-testid="open-model-settings"]').trigger('click')
    await flushPromises()

    const dialog = wrapper.get('[data-testid="model-settings-dialog"]')
    expect(dialog.get('[data-testid="model-base-url"]').element).toHaveProperty(
      'value',
      'https://gateway.example.com/v1',
    )
    expect(dialog.get('[data-testid="model-api-key"]').attributes('type')).toBe('password')
    expect(dialog.get('[data-testid="model-name"]').element).toHaveProperty('value', 'qwen-vl-max')

    await dialog.get('[data-testid="toggle-api-key"]').trigger('click')
    expect(dialog.get('[data-testid="model-api-key"]').attributes('type')).toBe('text')

    await dialog.get('[data-testid="restore-model-defaults"]').trigger('click')
    expect(dialog.get('[data-testid="model-name"]').element).toHaveProperty('value', 'qwen3.7-plus')

    await dialog.get('[data-testid="model-base-url"]').setValue('https://new.example.com/v1')
    await dialog.get('[data-testid="model-api-key"]').setValue('new-company-key')
    await dialog.get('[data-testid="model-name"]').setValue('qwen-plus')
    expect(dialog.get('[data-testid="save-model-settings"]').attributes('disabled')).toBeUndefined()
    await dialog.get('[data-testid="save-model-settings"]').trigger('click')
    await flushPromises()

    expect(wrapper.find('[data-testid="model-settings-dialog"]').exists()).toBe(false)
    expect(wrapper.get('[data-testid="model-settings-notice"]').text()).toContain('已保存')

    await vi.advanceTimersByTimeAsync(4_000)
    expect(wrapper.find('[data-testid="model-settings-notice"]').exists()).toBe(false)

    await wrapper.get('[data-testid="open-model-settings"]').trigger('click')
    await flushPromises()
    expect(wrapper.get('[data-testid="model-name"]').element).toHaveProperty('value', 'qwen-plus')
  })

  it('keeps model settings open when the backdrop is clicked', async () => {
    const service = new FakeDesktopService()
    const wrapper = mountApp(service)
    await flushPromises()

    await wrapper.get('[data-testid="open-model-settings"]').trigger('click')
    await flushPromises()
    await wrapper.get('.settings-backdrop').trigger('click')

    expect(wrapper.find('[data-testid="model-settings-dialog"]').exists()).toBe(true)
    await wrapper.get('[data-testid="model-settings-cancel"]').trigger('click')
    expect(wrapper.find('[data-testid="model-settings-dialog"]').exists()).toBe(false)

    await wrapper.get('[data-testid="open-model-settings"]').trigger('click')
    await flushPromises()
    await wrapper.get('[aria-label="关闭设置"]').trigger('click')
    expect(wrapper.find('[data-testid="model-settings-dialog"]').exists()).toBe(false)
  })
})
