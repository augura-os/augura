import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import type { UpdateSettingsRequest } from "@shared";
import { fetchSettings, updateSettings } from "../services/api";

export function useSettings() {
  return useQuery({
    queryKey: ["settings"],
    queryFn: fetchSettings,
  });
}

export function useUpdateSettings() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (body: UpdateSettingsRequest) => updateSettings(body),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ["settings"] });
    },
  });
}
