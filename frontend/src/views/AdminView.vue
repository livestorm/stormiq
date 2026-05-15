<script setup>
import { computed, onMounted, ref } from "vue";
import { api } from "../api";
import { useWorkspace } from "../store/workspace";

const { state } = useWorkspace();

const activeTab = ref("users");
const users = ref([]);
const sessions = ref([]);
const loadingUsers = ref(false);
const loadingSessions = ref(false);
const error = ref("");
const promoteEmail = ref("");
const promoteUserId = ref("");
const promoting = ref(false);
const promoteError = ref("");
const promoteSuccess = ref("");
const deletingSessionId = ref(null);
const deleteConfirmId = ref(null);
const sessionSearch = ref("");
const userSearch = ref("");

async function loadUsers() {
  loadingUsers.value = true;
  error.value = "";
  try {
    const data = await api.adminGetUsers();
    users.value = data?.users || [];
  } catch (e) {
    error.value = e.message || "Failed to load users.";
  } finally {
    loadingUsers.value = false;
  }
}

async function loadSessions() {
  loadingSessions.value = true;
  error.value = "";
  try {
    const data = await api.adminGetSessions();
    sessions.value = data?.sessions || [];
  } catch (e) {
    error.value = e.message || "Failed to load sessions.";
  } finally {
    loadingSessions.value = false;
  }
}

async function promoteUser() {
  const email = promoteEmail.value.trim();
  if (!email) return;
  promoting.value = true;
  promoteError.value = "";
  promoteSuccess.value = "";
  try {
    await api.adminPromoteUser(email, promoteUserId.value.trim());
    promoteSuccess.value = `${email} promoted to admin.`;
    promoteEmail.value = "";
    promoteUserId.value = "";
    await loadUsers();
  } catch (e) {
    promoteError.value = e.message || "Failed to promote user.";
  } finally {
    promoting.value = false;
  }
}

async function demoteUser(email) {
  if (!confirm(`Remove admin from ${email}?`)) return;
  try {
    await api.adminDemoteUser(email);
    await loadUsers();
  } catch (e) {
    error.value = e.message || "Failed to demote user.";
  }
}

async function deleteSession(sessionId) {
  if (deleteConfirmId.value !== sessionId) {
    deleteConfirmId.value = sessionId;
    return;
  }
  deletingSessionId.value = sessionId;
  deleteConfirmId.value = null;
  try {
    await api.adminDeleteSession(sessionId);
    sessions.value = sessions.value.filter((s) => s.session_id !== sessionId);
  } catch (e) {
    error.value = e.message || "Failed to delete session.";
  } finally {
    deletingSessionId.value = null;
  }
}

function cancelDeleteConfirm(sessionId) {
  if (deleteConfirmId.value === sessionId) deleteConfirmId.value = null;
}

const filteredSessions = computed(() => {
  const q = sessionSearch.value.trim().toLowerCase();
  if (!q) return sessions.value;
  return sessions.value.filter(
    (s) =>
      (s.session_id || "").toLowerCase().includes(q) ||
      (s.event_title || "").toLowerCase().includes(q) ||
      (s.created_by_email || "").toLowerCase().includes(q) ||
      (s.organization_id || "").toLowerCase().includes(q)
  );
});

const filteredUsers = computed(() => {
  const q = userSearch.value.trim().toLowerCase();
  if (!q) return users.value;
  return users.value.filter(
    (u) =>
      (u.email || "").toLowerCase().includes(q) ||
      (u.organization_id || "").toLowerCase().includes(q) ||
      (u.user_id || "").toLowerCase().includes(q) ||
      (u.profile?.full_name || "").toLowerCase().includes(q)
  );
});

function switchTab(tab) {
  activeTab.value = tab;
  error.value = "";
  if (tab === "users" && users.value.length === 0) loadUsers();
  if (tab === "sessions" && sessions.value.length === 0) loadSessions();
}

function formatDate(ts) {
  if (!ts) return "—";
  try {
    return new Date(ts).toLocaleDateString("en-GB", { day: "2-digit", month: "short", year: "numeric" });
  } catch {
    return ts;
  }
}

function contentFlags(session) {
  const flags = [];
  if (session.has_transcript) flags.push("Transcript");
  if (session.has_overall) flags.push("Overall");
  if (session.has_deep) flags.push("Deep");
  if (session.has_recap) flags.push("Recap");
  if (session.has_repurposing) flags.push("Content");
  if (session.has_cover) flags.push("Cover");
  return flags;
}

onMounted(() => {
  loadUsers();
});
</script>

<template>
  <div class="admin-view">
    <div class="admin-header">
      <h2 class="admin-title">Admin Panel</h2>
      <p class="admin-subtitle">Manage users and sessions across your StormIQ workspace.</p>
    </div>

    <div v-if="!state.auth?.isAdmin" class="admin-denied">
      <p>You do not have admin access.</p>
    </div>

    <template v-else>
      <div class="admin-tabs">
        <button
          class="admin-tab"
          :class="{ active: activeTab === 'users' }"
          @click="switchTab('users')"
        >
          Users
        </button>
        <button
          class="admin-tab"
          :class="{ active: activeTab === 'sessions' }"
          @click="switchTab('sessions')"
        >
          Sessions
        </button>
      </div>

      <p v-if="error" class="admin-error">{{ error }}</p>

      <!-- ── Users tab ── -->
      <div v-if="activeTab === 'users'" class="admin-section">
        <div class="admin-promote-form">
          <h3 class="admin-section-title">Promote to Admin</h3>
          <div class="promote-fields">
            <input
              v-model="promoteEmail"
              type="email"
              placeholder="Email address"
              class="admin-input"
              @keyup.enter="promoteUser"
            />
            <input
              v-model="promoteUserId"
              type="text"
              placeholder="Livestorm User ID (optional)"
              class="admin-input"
            />
            <button class="admin-btn primary" :disabled="promoting || !promoteEmail.trim()" @click="promoteUser">
              {{ promoting ? "Promoting…" : "Promote" }}
            </button>
          </div>
          <p v-if="promoteError" class="admin-error">{{ promoteError }}</p>
          <p v-if="promoteSuccess" class="admin-success">{{ promoteSuccess }}</p>
        </div>

        <div class="admin-toolbar">
          <input
            v-model="userSearch"
            type="search"
            placeholder="Search users…"
            class="admin-search"
          />
          <span class="admin-count">{{ filteredUsers.length }} user{{ filteredUsers.length !== 1 ? "s" : "" }}</span>
        </div>

        <div v-if="loadingUsers" class="admin-loading">Loading users…</div>
        <div v-else-if="filteredUsers.length === 0" class="admin-empty">No users found.</div>
        <div v-else class="admin-table-wrap">
          <table class="admin-table">
            <thead>
              <tr>
                <th>Email</th>
                <th>Org ID</th>
                <th>User ID</th>
                <th>Sessions</th>
                <th>Last seen</th>
                <th>Admin</th>
                <th></th>
              </tr>
            </thead>
            <tbody>
              <tr v-for="user in filteredUsers" :key="user.email">
                <td class="cell-email">{{ user.email || "—" }}</td>
                <td class="cell-id">{{ user.organization_id || "—" }}</td>
                <td class="cell-id">{{ user.user_id || "—" }}</td>
                <td class="cell-num">{{ user.session_count ?? "—" }}</td>
                <td class="cell-date">{{ formatDate(user.updated_at) }}</td>
                <td>
                  <span v-if="user.is_admin" class="badge badge-admin">Admin</span>
                  <span v-else class="badge badge-user">User</span>
                </td>
                <td>
                  <button
                    v-if="user.is_admin && user.email !== state.auth?.connectedUser?.email"
                    class="admin-btn danger small"
                    @click="demoteUser(user.email)"
                  >
                    Revoke
                  </button>
                  <button
                    v-else-if="!user.is_admin"
                    class="admin-btn secondary small"
                    @click="promoteEmail = user.email; promoteUserId = user.user_id || ''"
                  >
                    Promote
                  </button>
                </td>
              </tr>
            </tbody>
          </table>
        </div>
      </div>

      <!-- ── Sessions tab ── -->
      <div v-if="activeTab === 'sessions'" class="admin-section">
        <div class="admin-toolbar">
          <input
            v-model="sessionSearch"
            type="search"
            placeholder="Search sessions…"
            class="admin-search"
          />
          <button class="admin-btn secondary" @click="loadSessions">Refresh</button>
          <span class="admin-count">{{ filteredSessions.length }} session{{ filteredSessions.length !== 1 ? "s" : "" }}</span>
        </div>

        <div v-if="loadingSessions" class="admin-loading">Loading sessions…</div>
        <div v-else-if="filteredSessions.length === 0 && sessions.length === 0" class="admin-empty">
          No sessions cached yet.
          <button class="admin-btn secondary" style="margin-left: 12px" @click="loadSessions">Load</button>
        </div>
        <div v-else-if="filteredSessions.length === 0" class="admin-empty">No sessions match your search.</div>
        <div v-else class="admin-table-wrap">
          <table class="admin-table">
            <thead>
              <tr>
                <th>Session ID</th>
                <th>Event title</th>
                <th>Org ID</th>
                <th>Created by</th>
                <th>Updated</th>
                <th>Content</th>
                <th></th>
              </tr>
            </thead>
            <tbody>
              <tr
                v-for="session in filteredSessions"
                :key="session.session_id"
                :class="{ 'row-deleting': deletingSessionId === session.session_id }"
              >
                <td class="cell-id">
                  <a
                    :href="`/single-analysis/${session.session_id}/session-overview`"
                    class="session-link"
                    target="_blank"
                    rel="noopener"
                  >{{ session.session_id }}</a>
                </td>
                <td>{{ session.event_title || "—" }}</td>
                <td class="cell-id">{{ session.organization_id || "—" }}</td>
                <td class="cell-email">{{ session.created_by_email || "—" }}</td>
                <td class="cell-date">{{ formatDate(session.updated_at) }}</td>
                <td>
                  <div class="content-flags">
                    <span
                      v-for="flag in contentFlags(session)"
                      :key="flag"
                      class="badge badge-content"
                    >{{ flag }}</span>
                    <span v-if="contentFlags(session).length === 0" class="badge badge-empty">Base only</span>
                  </div>
                </td>
                <td>
                  <div class="delete-cell">
                    <button
                      v-if="deleteConfirmId !== session.session_id"
                      class="admin-btn danger small"
                      :disabled="deletingSessionId === session.session_id"
                      @click="deleteSession(session.session_id)"
                    >
                      {{ deletingSessionId === session.session_id ? "Deleting…" : "Delete" }}
                    </button>
                    <template v-else>
                      <button class="admin-btn danger small" @click="deleteSession(session.session_id)">Confirm</button>
                      <button class="admin-btn secondary small" @click="cancelDeleteConfirm(session.session_id)">Cancel</button>
                    </template>
                  </div>
                </td>
              </tr>
            </tbody>
          </table>
        </div>
      </div>
    </template>
  </div>
</template>

<style scoped>
.admin-view {
  padding: 32px 40px;
  max-width: 1200px;
  margin: 0 auto;
}

.admin-header {
  margin-bottom: 28px;
}

.admin-title {
  font-size: 22px;
  font-weight: 700;
  margin: 0 0 4px;
  color: var(--color-text-primary, #1a1a2e);
}

.admin-subtitle {
  font-size: 14px;
  color: var(--color-text-secondary, #666);
  margin: 0;
}

.admin-denied {
  padding: 40px;
  text-align: center;
  color: var(--color-text-secondary, #666);
}

.admin-tabs {
  display: flex;
  gap: 4px;
  margin-bottom: 24px;
  border-bottom: 1px solid var(--color-border, #e5e7eb);
  padding-bottom: 0;
}

.admin-tab {
  padding: 10px 20px;
  font-size: 14px;
  font-weight: 500;
  background: none;
  border: none;
  border-bottom: 2px solid transparent;
  cursor: pointer;
  color: var(--color-text-secondary, #666);
  transition: color 120ms, border-color 120ms;
  margin-bottom: -1px;
}

.admin-tab:hover {
  color: var(--color-text-primary, #1a1a2e);
}

.admin-tab.active {
  color: #0b42c3;
  border-bottom-color: #0b42c3;
}

.admin-section {
  display: flex;
  flex-direction: column;
  gap: 20px;
}

.admin-section-title {
  font-size: 15px;
  font-weight: 600;
  margin: 0 0 12px;
}

.admin-promote-form {
  background: var(--color-surface, #f9fafb);
  border: 1px solid var(--color-border, #e5e7eb);
  border-radius: 10px;
  padding: 20px;
}

.promote-fields {
  display: flex;
  gap: 10px;
  flex-wrap: wrap;
  align-items: center;
}

.admin-input {
  flex: 1;
  min-width: 200px;
  padding: 8px 12px;
  border: 1px solid var(--color-border, #d1d5db);
  border-radius: 6px;
  font-size: 14px;
  outline: none;
  background: #fff;
}

.admin-input:focus {
  border-color: #0b42c3;
  box-shadow: 0 0 0 2px rgba(11, 66, 195, 0.12);
}

.admin-toolbar {
  display: flex;
  align-items: center;
  gap: 12px;
  flex-wrap: wrap;
}

.admin-search {
  flex: 1;
  min-width: 180px;
  max-width: 360px;
  padding: 8px 12px;
  border: 1px solid var(--color-border, #d1d5db);
  border-radius: 6px;
  font-size: 14px;
  outline: none;
  background: #fff;
}

.admin-search:focus {
  border-color: #0b42c3;
  box-shadow: 0 0 0 2px rgba(11, 66, 195, 0.12);
}

.admin-count {
  font-size: 13px;
  color: var(--color-text-secondary, #888);
  margin-left: auto;
}

.admin-table-wrap {
  overflow-x: auto;
  border: 1px solid var(--color-border, #e5e7eb);
  border-radius: 10px;
}

.admin-table {
  width: 100%;
  border-collapse: collapse;
  font-size: 13px;
}

.admin-table th {
  text-align: left;
  padding: 10px 14px;
  background: var(--color-surface, #f9fafb);
  color: var(--color-text-secondary, #888);
  font-weight: 600;
  font-size: 12px;
  text-transform: uppercase;
  letter-spacing: 0.05em;
  border-bottom: 1px solid var(--color-border, #e5e7eb);
  white-space: nowrap;
}

.admin-table td {
  padding: 10px 14px;
  border-bottom: 1px solid var(--color-border, #f0f0f0);
  vertical-align: middle;
  color: var(--color-text-primary, #1a1a2e);
}

.admin-table tr:last-child td {
  border-bottom: none;
}

.admin-table tr:hover td {
  background: var(--color-surface, #f9fafb);
}

.admin-table tr.row-deleting td {
  opacity: 0.4;
}

.cell-id {
  font-family: monospace;
  font-size: 12px;
  color: var(--color-text-secondary, #666);
  max-width: 180px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.cell-email {
  max-width: 200px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.cell-date {
  white-space: nowrap;
  color: var(--color-text-secondary, #666);
}

.cell-num {
  text-align: right;
}

.session-link {
  color: #0b42c3;
  text-decoration: none;
}

.session-link:hover {
  text-decoration: underline;
}

.content-flags {
  display: flex;
  flex-wrap: wrap;
  gap: 4px;
}

.badge {
  display: inline-block;
  padding: 2px 7px;
  border-radius: 20px;
  font-size: 11px;
  font-weight: 600;
  white-space: nowrap;
}

.badge-admin {
  background: rgba(11, 66, 195, 0.12);
  color: #0b42c3;
}

.badge-user {
  background: rgba(0, 0, 0, 0.05);
  color: #666;
}

.badge-content {
  background: rgba(34, 197, 94, 0.12);
  color: #15803d;
}

.badge-empty {
  background: rgba(0, 0, 0, 0.05);
  color: #999;
}

.delete-cell {
  display: flex;
  gap: 6px;
  align-items: center;
}

.admin-btn {
  display: inline-flex;
  align-items: center;
  padding: 8px 16px;
  border-radius: 6px;
  font-size: 14px;
  font-weight: 500;
  border: none;
  cursor: pointer;
  transition: background 120ms;
  white-space: nowrap;
}

.admin-btn:disabled {
  opacity: 0.5;
  cursor: not-allowed;
}

.admin-btn.primary {
  background: #0b42c3;
  color: #fff;
}

.admin-btn.primary:hover:not(:disabled) {
  background: #0936a8;
}

.admin-btn.secondary {
  background: var(--color-surface, #f0f0f0);
  color: var(--color-text-primary, #333);
  border: 1px solid var(--color-border, #d1d5db);
}

.admin-btn.secondary:hover:not(:disabled) {
  background: #e5e7eb;
}

.admin-btn.danger {
  background: #ef4444;
  color: #fff;
}

.admin-btn.danger:hover:not(:disabled) {
  background: #dc2626;
}

.admin-btn.small {
  padding: 5px 10px;
  font-size: 12px;
}

.admin-loading,
.admin-empty {
  padding: 40px;
  text-align: center;
  color: var(--color-text-secondary, #888);
  font-size: 14px;
}

.admin-error {
  color: #dc2626;
  font-size: 13px;
  margin: 0;
}

.admin-success {
  color: #15803d;
  font-size: 13px;
  margin: 0;
}
</style>
