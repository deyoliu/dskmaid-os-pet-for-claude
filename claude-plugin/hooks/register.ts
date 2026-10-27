import type { Register } from 'claude-code'

// toggle_launcher.py (beside plugin.json's folder) finds the repo's toggle.py through the
// junction, so the repo can live anywhere. It prints one line for the transcript.
const LAUNCHER = 'toggle_launcher.py'
const MODES = ['on', 'off', 'status']

export const register: Register = on => {
  on('session.start', async ($, e, next) => {
    await $.command.register({
      name: 'dskmaid',
      description: '叫出或收起鯨魚娘桌寵（再輸入一次就關閉）',
      argumentHint: '[on|off|status]',
      immediate: true,
    })
    return next(e)
  })

  on('command.run', { command: 'dskmaid' }, async ($, e) => {
    const mode = e.args.trim().toLowerCase()
    if (mode && !MODES.includes(mode)) {
      return { text: '用法：/dskmaid [on|off|status]（不加參數就是切換）' }
    }
    try {
      const launcher = `${$.plugin.root.replace(/[\\/]+$/, '')}/${LAUNCHER}`
      const r = await $.process.run(['python', launcher, ...(mode ? [mode] : [])], { timeoutMs: 40_000 })
      const out = (r.stdout.trim() || r.stderr.trim().split('\n').slice(-3).join('\n')).trim()
      return { text: out || `toggle.py 結束碼 ${r.exitCode}，沒有輸出` }
    } catch (err) {
      return { text: `執行 toggle.py 失敗：${String(err)}` }
    }
  })
}
