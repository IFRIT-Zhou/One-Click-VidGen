import { requestJSON } from './api.js'

export function createPasswordRecoveryClient(request = requestJSON, now = Date.now) {
  let busy = false, retryAt = 0
  const remaining = () => Math.max(0, Math.ceil((retryAt - now()) / 1000))
  async function post(action, body) {
    if (busy) throw new Error('正在处理，请勿重复提交。')
    busy = true
    try {
      return await request(`/api/cloud/auth/password-reset/${action}`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body),
      })
    } catch (error) {
      if (error.status === 429) retryAt = now() + Math.max(1, Number(error.retryAfter) || 60) * 1000
      throw error
    } finally { busy = false }
  }
  return {
    remaining,
    async send(email) {
      if (remaining()) throw new Error(`请 ${remaining()} 秒后重新发送。`)
      const result = await post('request', { email: email.trim() })
      retryAt = now() + Math.max(1, Number(result.retry_after) || 60) * 1000
      return result
    },
    async confirm(email, code, password, repeated) {
      if (!/^[0-9]{6}$/.test(code.trim())) throw new Error('请输入 6 位数字验证码。')
      if (password.length < 10 || password.length > 200) throw new Error('新密码需要 10 至 200 位字符。')
      if (password !== repeated) throw new Error('两次输入的新密码不一致。')
      return post('confirm', { email: email.trim(), code: code.trim(), password })
    },
  }
}
