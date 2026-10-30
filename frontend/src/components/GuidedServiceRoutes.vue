<template>
  <section class="guided-service-routes" aria-label="本项目生成渠道">
    <div class="route-heading">
      <strong>本项目生成渠道</strong>
      <button type="button" class="ghost-btn" :disabled="busy" @click="$emit('change', { language_route: 'cloud', task_image_pool: true })">文本与图片都用号池</button>
    </div>
    <p v-if="form.use_cloud_image_pool && (routes.language_route !== 'cloud' || !routes.use_cloud_image_pool)" class="route-warning">
      全局号池已开启，但本项目仍有个人 API 配置。请在这里切换；不需要重新配音。
    </p>
    <div class="route-fields">
      <label>语言模型
        <select :value="routes.language_route" :disabled="busy" @change="$emit('change', { language_route: $event.target.value })">
          <option value="cloud">云端语言模型号池</option>
          <option value="personal">个人语言模型 API</option>
        </select>
      </label>
      <label>图像生成
        <select :value="routes.use_cloud_image_pool ? 'cloud' : 'personal'" :disabled="busy" @change="$emit('change', { task_image_pool: $event.target.value === 'cloud' })">
          <option value="cloud">云端图像号池</option>
          <option value="personal">个人图像 API</option>
        </select>
      </label>
      <label v-if="routes.use_cloud_image_pool">号池图片渠道
        <select :value="form.method" :disabled="busy" @change="$emit('change', { method: $event.target.value })">
          <option value="ican">ICAN</option>
          <option value="running">稳定版</option>
        </select>
      </label>
    </div>
    <p v-if="!authenticated && (routes.language_route === 'cloud' || routes.use_cloud_image_pool)" class="route-warning">请先在“接口与服务”登录云端账户，再继续生成。</p>
    <small>点击“确认设置并开始作图”时保存并使用以上渠道。只影响本项目，不重配音，不修改其他项目。</small>
  </section>
</template>

<script setup>
import { computed } from 'vue'
import { guidedServiceRoutes } from '../guidedServiceRoutes.js'
const props = defineProps({ form: { type: Object, required: true }, authenticated: Boolean, busy: Boolean })
defineEmits(['change'])
const routes = computed(() => guidedServiceRoutes(props.form))
</script>

<style scoped>
.guided-service-routes { margin-top: 16px; padding: 16px; border: 1px solid var(--border, #354844); border-radius: 12px; }
.route-heading { display: flex; flex-wrap: wrap; align-items: center; justify-content: space-between; gap: 12px; }
.route-fields { display: grid; grid-template-columns: repeat(auto-fit, minmax(180px, 1fr)); gap: 12px; margin: 14px 0; }
label { display: grid; gap: 8px; }
select { width: 100%; min-width: 0; }
.route-warning { color: #efc97b; margin: 12px 0; }
small { opacity: .8; line-height: 1.6; }
</style>
