<template>
  <div class="cloud-password-recovery">
    <template v-if="success">
      <p role="status">密码已重置，原登录已失效。请使用新密码重新登录。</p>
      <button class="primary-btn" type="button" @click="$emit('back', email)">返回登录</button>
    </template>
    <form v-else class="cloud-auth-form" @submit.prevent="confirm">
      <p class="muted">输入账户绑定的邮箱，获取验证码后设置新密码。</p>
      <label><span>绑定邮箱</span><input ref="emailInput" v-model.trim="email" type="email" autocomplete="email" maxlength="254" required :readonly="busy" @input="code = ''" /></label>
      <label><span>邮箱验证码</span><div class="recovery-code-row"><input v-model.trim="code" type="text" inputmode="numeric" autocomplete="one-time-code" pattern="[0-9]{6}" maxlength="6" placeholder="6 位数字验证码" required /><button class="ghost-btn" type="button" :disabled="busy || countdown > 0" @click="send">{{ countdown ? `${countdown} 秒后重发` : '发送验证码' }}</button></div></label>
      <p class="muted">验证码 30 分钟内有效，仅可使用一次。重新发送后请使用最新验证码。</p>
      <label><span>新密码</span><input v-model="password" type="password" autocomplete="new-password" minlength="10" maxlength="200" placeholder="10 至 200 位字符" required /></label>
      <label><span>确认新密码</span><input v-model="repeated" type="password" autocomplete="new-password" minlength="10" maxlength="200" required /></label>
      <p v-if="notice" :class="failed ? 'board-error' : 'api-key-message'" role="status">{{ notice }}</p>
      <button class="primary-btn" type="submit" :disabled="busy">{{ busy ? '正在处理…' : '重置密码' }}</button>
      <button class="ghost-btn" type="button" :disabled="busy" @click="$emit('back', email)">返回登录</button>
    </form>
  </div>
</template>

<script setup>
import { ref, onMounted, onUnmounted } from 'vue'
import { createPasswordRecoveryClient } from '../cloudPasswordRecovery.js'
const props = defineProps({ initialEmail: { type: String, default: '' } })
const emit = defineEmits(['back', 'reset'])
const email = ref(props.initialEmail), code = ref(''), password = ref(''), repeated = ref('')
const emailInput = ref(null), busy = ref(false), notice = ref(''), failed = ref(false), success = ref(false), countdown = ref(0)
const client = createPasswordRecoveryClient()
let timer, active = true
onMounted(() => { emailInput.value?.focus(); timer = setInterval(() => { countdown.value = client.remaining() }, 1000) })
onUnmounted(() => { active = false; clearInterval(timer); password.value = ''; repeated.value = ''; code.value = '' })
async function send() {
  if (busy.value || !emailInput.value.reportValidity()) return
  busy.value = true; notice.value = ''; failed.value = false
  try {
    await client.send(email.value)
    if (active) notice.value = '如果该邮箱已绑定可用账户，验证码将发送至邮箱，请检查收件箱或垃圾邮件。'
  } catch (error) { if (active) { failed.value = true; notice.value = error.message } }
  finally { if (active) { busy.value = false; countdown.value = client.remaining() } }
}
async function confirm() {
  if (busy.value) return
  busy.value = true; notice.value = ''; failed.value = false
  try {
    await client.confirm(email.value, code.value, password.value, repeated.value)
    if (active) { success.value = true; code.value = ''; password.value = ''; repeated.value = ''; emit('reset', email.value) }
  } catch (error) { if (active) { failed.value = true; notice.value = error.message } }
  finally { if (active) busy.value = false }
}
</script>

<style>
.cloud-password-recovery .recovery-code-row{display:flex;gap:10px;align-items:center}.cloud-password-recovery .recovery-code-row input{min-width:0;flex:1}.cloud-password-recovery .recovery-code-row button{flex-shrink:0}.cloud-password-recovery p{line-height:1.6;grid-column:1/-1}.cloud-password-recovery .cloud-auth-form{gap:12px}
@media(max-width:460px){.cloud-password-recovery .recovery-code-row{flex-wrap:wrap}.cloud-password-recovery .recovery-code-row input{flex-basis:100%}}
</style>
