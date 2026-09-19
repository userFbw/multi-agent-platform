import { http } from './http'
import type { ApiRoundsResp, ApiStepsResp } from '@/types/api'

export const stepApi = {
  list: (projectId: number, roundNo?: number) =>
    http.get<ApiStepsResp>(`/api/projects/${projectId}/steps`, roundNo ? { round_no: roundNo } : undefined),

  latest: (projectId: number) =>
    http.get<ApiStepsResp>(`/api/projects/${projectId}/steps/latest`),

  rounds: (projectId: number) =>
    http.get<ApiRoundsResp>(`/api/projects/${projectId}/steps/rounds`)
}
