import { computed, ref } from 'vue'

import { listPlugins, PluginApiError, reloadPlugins, type RuntimePlugin } from '../api/plugins'

/** Owns the server-discovered plugin catalog and explicit reload requests for the workbench. */
export function usePluginCatalog() {
  const plugins = ref<RuntimePlugin[]>([])
  const errorMessage = ref<string | null>(null)
  const isLoading = ref(false)
  const reloadingPluginIds = ref<string[]>([])

  const isReloading = computed(() => reloadingPluginIds.value.length > 0)
  const reloadablePlugins = computed(() => plugins.value.filter((plugin) => plugin.reloadable))

  async function refresh(): Promise<void> {
    isLoading.value = true
    errorMessage.value = null
    try {
      plugins.value = await listPlugins()
    } catch (error) {
      errorMessage.value = displayError(error)
    } finally {
      isLoading.value = false
    }
  }

  async function reload(pluginIds: string[]): Promise<void> {
    const requestedIds = [...new Set(pluginIds)].filter((pluginId) => pluginId.length > 0)
    if (requestedIds.length === 0 || isReloading.value) {
      return
    }

    reloadingPluginIds.value = requestedIds
    errorMessage.value = null
    try {
      await reloadPlugins(requestedIds)
      await refresh()
    } catch (error) {
      errorMessage.value = displayError(error)
    } finally {
      reloadingPluginIds.value = []
    }
  }

  return {
    errorMessage,
    isLoading,
    isReloading,
    plugins,
    reloadingPluginIds,
    reload,
    reloadablePlugins,
    refresh,
  }
}

function displayError(error: unknown): string {
  if (error instanceof PluginApiError) {
    return error.message
  }
  return '无法读取插件状态。'
}
