import { computed, ref } from 'vue'

import {
  listPlugins,
  PluginApiError,
  reloadPlugins,
  setPluginActivation,
  type RuntimePlugin,
} from '../api/plugins'

/** Owns the server-discovered plugin catalog and explicit reload requests for the workbench. */
export function usePluginCatalog() {
  const plugins = ref<RuntimePlugin[]>([])
  const errorMessage = ref<string | null>(null)
  const isLoading = ref(false)
  const reloadingPluginIds = ref<string[]>([])
  const activatingPluginIds = ref<string[]>([])

  const isReloading = computed(() => reloadingPluginIds.value.length > 0)
  const isActivating = computed(() => activatingPluginIds.value.length > 0)
  const reloadablePlugins = computed(() => (
    plugins.value.filter((plugin) => plugin.enabled && plugin.reloadable)
  ))

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
    if (requestedIds.length === 0 || isReloading.value || isActivating.value) {
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

  /** Applies one server-owned activation transition and replaces only that catalog entry. */
  async function setActivation(pluginId: string, enabled: boolean): Promise<void> {
    if (
      !pluginId
      || activatingPluginIds.value.includes(pluginId)
      || reloadingPluginIds.value.includes(pluginId)
    ) {
      return
    }

    activatingPluginIds.value = [...activatingPluginIds.value, pluginId]
    errorMessage.value = null
    try {
      const updatedPlugin = await setPluginActivation(pluginId, enabled)
      const pluginIndex = plugins.value.findIndex((plugin) => plugin.pluginId === updatedPlugin.pluginId)
      if (pluginIndex === -1) {
        plugins.value = [...plugins.value, updatedPlugin]
      } else {
        plugins.value.splice(pluginIndex, 1, updatedPlugin)
      }
    } catch (error) {
      errorMessage.value = displayError(error)
    } finally {
      activatingPluginIds.value = activatingPluginIds.value.filter((id) => id !== pluginId)
    }
  }

  return {
    activatingPluginIds,
    errorMessage,
    isActivating,
    isLoading,
    isReloading,
    plugins,
    reloadingPluginIds,
    reload,
    reloadablePlugins,
    refresh,
    setActivation,
  }
}

function displayError(error: unknown): string {
  if (error instanceof PluginApiError) {
    return error.message
  }
  return '无法读取插件状态。'
}
