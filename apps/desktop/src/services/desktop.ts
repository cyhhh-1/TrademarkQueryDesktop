import type { InjectionKey } from 'vue'
import { invoke } from '@tauri-apps/api/core'
import { listen as listenEvent } from '@tauri-apps/api/event'
import { getCurrentWindow } from '@tauri-apps/api/window'

import type { WorkerEvent } from '../domain/types'

export interface ModelSettings {
  DASHSCOPE_BASE_URL: string
  DASHSCOPE_API_KEY: string
  DASHSCOPE_MODEL: string
}

export interface ModelSettingsState {
  current: ModelSettings
  defaults: ModelSettings
}

export interface DesktopService {
  startWorker(): Promise<void>
  stopWorker(): Promise<void>
  listen(handler: (event: WorkerEvent) => void): Promise<() => void>
  pickXlsx(): Promise<string | null>
  send(command: Record<string, unknown>): Promise<void>
  saveOutputAs(source: string, input?: string): Promise<string | null>
  loadModelSettings(): Promise<ModelSettingsState>
  saveModelSettings(settings: ModelSettings): Promise<ModelSettings>
  notify(title: string, body: string): Promise<void>
  minimizeApp(): Promise<void>
}

export const DESKTOP_SERVICE_KEY: InjectionKey<DesktopService> = Symbol('desktopService')

export function createDesktopService(): DesktopService {
  return '__TAURI_INTERNALS__' in window ? new TauriDesktopService() : new PreviewDesktopService()
}

class TauriDesktopService implements DesktopService {
  async startWorker(): Promise<void> {
    await invoke('worker_start')
  }

  async stopWorker(): Promise<void> {
    await invoke('worker_stop')
  }

  async listen(handler: (event: WorkerEvent) => void): Promise<() => void> {
    return listenEvent<WorkerEvent>('worker-event', (event) => handler(event.payload))
  }

  async pickXlsx(): Promise<string | null> {
    return invoke<string | null>('pick_xlsx')
  }

  async send(command: Record<string, unknown>): Promise<void> {
    await invoke('worker_send', { command })
  }

  async saveOutputAs(source: string, input?: string): Promise<string | null> {
    return invoke<string | null>('save_output_as', { source, input: input ?? null })
  }

  async loadModelSettings(): Promise<ModelSettingsState> {
    return invoke<ModelSettingsState>('load_model_settings')
  }

  async saveModelSettings(settings: ModelSettings): Promise<ModelSettings> {
    return invoke<ModelSettings>('save_model_settings', { settings })
  }

  async notify(title: string, body: string): Promise<void> {
    const { isPermissionGranted, requestPermission, sendNotification } = await import(
      '@tauri-apps/plugin-notification'
    )
    let granted = await isPermissionGranted()
    if (!granted) granted = (await requestPermission()) === 'granted'
    if (granted) sendNotification({ title, body })
  }

  async minimizeApp(): Promise<void> {
    await getCurrentWindow().minimize()
  }
}

class PreviewDesktopService implements DesktopService {
  private listener: ((event: WorkerEvent) => void) | null = null

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
    return null
  }

  async send(): Promise<void> {
    throw new Error('请在商标查询助手桌面程序中使用此功能')
  }

  async saveOutputAs(): Promise<string | null> {
    return null
  }

  async loadModelSettings(): Promise<ModelSettingsState> {
    const defaults = {
      DASHSCOPE_BASE_URL: 'https://dashscope.aliyuncs.com/compatible-mode/v1',
      DASHSCOPE_API_KEY: 'configured-in-desktop-app',
      DASHSCOPE_MODEL: 'qwen3.7-plus',
    }
    return { current: { ...defaults }, defaults }
  }

  async saveModelSettings(settings: ModelSettings): Promise<ModelSettings> {
    return { ...settings }
  }

  async notify(): Promise<void> {}

  async minimizeApp(): Promise<void> {}
}
