import { describe, expect, it } from 'vitest'

import { initialUiState, reduceWorkerEvent, taskCounts } from '../domain/taskState'
import type { UiState } from '../domain/types'


function runningState(): UiState {
  return {
    ...initialUiState(),
    taskState: 'running',
    rows: [
      { row: 2, mark: '已完成商标', applicant: '甲公司', phase: 'success', result: '查询成功' },
      { row: 3, mark: '当前商标', applicant: '乙公司', phase: 'running', result: '-' },
    ],
  }
}


describe('reduceWorkerEvent', () => {
  it('a second workbook clears the previous login preflight and run id', () => {
    const previous = {
      ...initialUiState(),
      browserState: 'READY' as const,
      taskState: 'completed' as const,
      runId: 'previous-run',
    }
    const next = reduceWorkerEvent(previous, {
      type: 'file_inspected',
      path: 'D:/batch-two.xlsx',
      mode: 'uniqueness',
      task_count: 1,
      size: 1024,
      rows: [{ row: 2, mark: 'MARK' }],
    })

    expect(next.browserState).toBe('WAITING_SITE')
    expect(next.taskState).toBe('waiting_browser')
    expect(next.runId).toBeNull()
  })
  it('登录失效时立即暂停并要求重新确认登录', () => {
    const next = reduceWorkerEvent(runningState(), {
      type: 'paused',
      reason: 'LOGIN_REQUIRED',
      recoverable: true,
      row: 3,
    })

    expect(next.taskState).toBe('paused_user_action')
    expect(next.browserState).toBe('LOGIN_REQUIRED')
    expect(next.rows[0].phase).toBe('success')
    expect(next.rows[1].phase).toBe('paused')
    expect(next.alert).toContain('重新登录')
  })

  it('文件检测结果建立真实任务行', () => {
    const next = reduceWorkerEvent(initialUiState(), {
      type: 'file_inspected',
      path: 'D:/资料/商标申请.xlsx',
      mode: 'application',
      task_count: 2,
      size: 2048,
      rows: [
        { row: 2, mark: '云杉月付', applicant: '北京云杉网络技术有限公司' },
        { row: 5, mark: '放心借', applicant: '北京放心借科技有限公司' },
      ],
    })

    expect(next.file?.name).toBe('商标申请.xlsx')
    expect(next.file?.templateName).toBe('商标申请查询')
    expect(next.rows.map((row) => row.row)).toEqual([2, 5])
    expect(next.rows.map((row) => row.applicant)).toEqual([
      '北京云杉网络技术有限公司',
      '北京放心借科技有限公司',
    ])
    expect(taskCounts(next.rows).pending).toBe(2)
  })

  it('完成行只替换对应行并更新结果', () => {
    const next = reduceWorkerEvent(runningState(), {
      type: 'row_progress',
      phase: 'completed',
      row: 3,
      mark: '当前商标',
      status: 'success',
      term: '2025年至2035年',
    })

    expect(next.rows[0].phase).toBe('success')
    expect(next.rows[1]).toMatchObject({
      phase: 'success',
      result: '2025年至2035年',
    })
  })

  it('唯一性查询完成后显示识别到的商标申请人', () => {
    const state: UiState = {
      ...initialUiState(),
      rows: [
        { row: 2, mark: '青猫', applicant: '', phase: 'running', result: '-' },
      ],
    }
    const next = reduceWorkerEvent(state, {
      type: 'row_progress',
      phase: 'completed',
      row: 2,
      mark: '青猫',
      applicant: '北京天龙天天洁城市服务科技有限公司',
      status: 'success',
      uniqueness: '是',
    })

    expect(next.rows[0]).toMatchObject({
      applicant: '北京天龙天天洁城市服务科技有限公司',
      result: '是',
    })
  })

  it('完成事件保存输出路径并进入完成态', () => {
    const next = reduceWorkerEvent(runningState(), {
      type: 'completed',
      state: 'completed',
      output_path: 'D:/结果/商标申请_已查询.xlsx',
    })

    expect(next.taskState).toBe('completed')
    expect(next.outputPath).toContain('_已查询.xlsx')
  })

  it('批量查询事件保留当前序号商标和处理阶段', () => {
    const started = reduceWorkerEvent(runningState(), {
      type: 'row_progress',
      phase: 'started',
      row: 8,
      mark: '第七个商标',
      current: 7,
      total: 12,
    })
    const staged = reduceWorkerEvent(started, {
      type: 'row_progress',
      phase: 'stage',
      row: 8,
      mark: '第七个商标',
      current: 7,
      total: 12,
      stage: 'capturing_evidence',
    })

    expect(staged.progress).toEqual({
      current: 7,
      total: 12,
      currentMark: '第七个商标',
      stage: 'capturing_evidence',
    })
    expect(staged.rows.find((row) => row.row === 8)).toMatchObject({
      phase: 'running',
      result: '正在生成证据截图',
    })
  })

  it('安全取消后保留阶段性结果路径供另存为', () => {
    const next = reduceWorkerEvent(runningState(), {
      type: 'task_state',
      state: 'cancelled',
      run_id: 'batch',
      current: 1,
      total: 2,
      output_path: 'D:/结果/批量商标申请_阶段性结果.xlsx',
    })

    expect(next.taskState).toBe('cancelled')
    expect(next.outputPath).toContain('阶段性结果.xlsx')
    expect(next.progress).toMatchObject({ current: 1, total: 2 })
  })
})
