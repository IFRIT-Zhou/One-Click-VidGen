export function taskImagePoolPreference(form) {
  return Boolean(typeof form.task_image_pool === 'boolean'
    ? form.task_image_pool : form.use_cloud_image_pool)
}

export function taskImagePoolEnabled(form, authenticated) {
  return Boolean(authenticated && taskImagePoolPreference(form))
}
