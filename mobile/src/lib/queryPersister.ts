import AsyncStorage from '@react-native-async-storage/async-storage'
import { createAsyncStoragePersister } from '@tanstack/query-async-storage-persister'

export const queryPersister = createAsyncStoragePersister({
  storage: AsyncStorage,
  key: 'seventh_ai_query_cache',
  throttleTime: 1_000,
})
