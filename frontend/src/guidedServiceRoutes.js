import { taskImagePoolPreference } from './taskImagePool.js'

export function guidedServiceRoutes(form) {
  const image = taskImagePoolPreference(form)
  const language = ['cloud', 'personal'].includes(form.language_route)
    ? form.language_route
    : form.language_route === 'project'
      ? (image ? 'cloud' : 'personal')
      : (form.use_cloud_image_pool ? 'cloud' : 'personal')
  return { language_route: language, use_cloud_image_pool: image }
}

export function guidedServiceParameters(form, authenticated) {
  const routes = guidedServiceRoutes(form)
  if (!authenticated && (routes.language_route === 'cloud' || routes.use_cloud_image_pool)) {
    throw new Error('本项目已选择云端号池，请先在“接口与服务”登录云端账户；未切换为个人 API。')
  }
  return routes
}
