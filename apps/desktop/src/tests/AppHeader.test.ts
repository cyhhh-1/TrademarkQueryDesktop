import { flushPromises, mount } from '@vue/test-utils'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import AppHeader from '../components/AppHeader.vue'


const windowMocks = vi.hoisted(() => ({
  close: vi.fn(async () => undefined),
  minimize: vi.fn(async () => undefined),
  startDragging: vi.fn(async () => undefined),
  toggleMaximize: vi.fn(async () => undefined),
}))

vi.mock('@tauri-apps/api/window', () => ({
  getCurrentWindow: () => windowMocks,
}))


describe('AppHeader', () => {
  beforeEach(() => {
    Object.defineProperty(window, '__TAURI_INTERNALS__', {
      configurable: true,
      value: {},
    })
    vi.clearAllMocks()
  })

  afterEach(() => {
    Reflect.deleteProperty(window, '__TAURI_INTERNALS__')
  })

  it('starts native dragging when the employee drags the header background', async () => {
    const wrapper = mount(AppHeader)

    await wrapper.get('header').trigger('mousedown', { button: 0 })
    await flushPromises()

    expect(windowMocks.startDragging).toHaveBeenCalledOnce()
  })

  it('loads the packaged header icon from a CSP-safe app resource', () => {
    const wrapper = mount(AppHeader)

    const iconSource = wrapper.get('.brand-mark').attributes('src')
    expect(iconSource).toContain('app-icon.svg')
    expect(iconSource).not.toContain('data:')
  })

  it('keeps the three native window actions connected', async () => {
    const wrapper = mount(AppHeader)

    await wrapper.get('[aria-label="最小化"]').trigger('click')
    await wrapper.get('[aria-label="最大化"]').trigger('click')
    await wrapper.get('[aria-label="关闭"]').trigger('click')
    await flushPromises()

    expect(windowMocks.minimize).toHaveBeenCalledOnce()
    expect(windowMocks.toggleMaximize).toHaveBeenCalledOnce()
    expect(windowMocks.close).toHaveBeenCalledOnce()
  })
})
