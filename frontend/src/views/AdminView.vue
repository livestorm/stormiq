<script setup>
import { computed, onMounted, ref } from "vue";
import { RouterLink } from "vue-router";
import Card from "../components/ui/Card.vue";
import CardHeader from "../components/ui/CardHeader.vue";
import Chip from "../components/ui/Chip.vue";
import Icon from "../components/ui/Icon.vue";
import StatusWord from "../components/ui/StatusWord.vue";
import UiButton from "../components/ui/UiButton.vue";
import { api } from "../api";
import { useWorkspace } from "../store/workspace";

// Admin: the people with access and who is an admin, every organization's
// sessions, and the AI models. Only admins reach it: every route behind it
// answers 403 to anyone else (`auth.require_admin`), and the top bar shows it
// only to admins.

const { state } = useWorkspace();

const TABS = [
  { value: "users", label: "People" },
  { value: "sessions", label: "Sessions" },
  { value: "organizations", label: "Organizations" },
  { value: "models", label: "Models" },
];
const activeTab = ref("users");

function formatDate(value) {
  if (!value) return "—";
  const date = new Date(value);
  return Number.isNaN(date.getTime())
    ? String(value)
    : date.toLocaleDateString("en-GB", { day: "numeric", month: "short", year: "numeric" });
}

function matches(needle, values) {
  const query = needle.trim().toLowerCase();
  return !query || values.some((value) => String(value || "").toLowerCase().includes(query));
}

// ── People ────────────────────────────────────────────────────────────────
const users = ref([]);
const loadingUsers = ref(false);
const usersError = ref("");
const userSearch = ref("");
const promoteEmail = ref("");
const promoteUserId = ref("");
const promoting = ref(false);
const promoteMessage = ref({ text: "", ok: false });
const revokeConfirm = ref("");

async function loadUsers() {
  loadingUsers.value = true;
  usersError.value = "";
  try {
    const data = await api.adminGetUsers();
    users.value = data?.users || [];
  } catch (error) {
    usersError.value = error?.message || "The people could not be loaded.";
  } finally {
    loadingUsers.value = false;
  }
}

async function promoteUser() {
  const email = promoteEmail.value.trim();
  if (!email) return;
  promoting.value = true;
  promoteMessage.value = { text: "", ok: false };
  try {
    await api.adminPromoteUser(email, promoteUserId.value.trim());
    promoteMessage.value = { text: `${email} is now an admin.`, ok: true };
    promoteEmail.value = "";
    promoteUserId.value = "";
    await loadUsers();
  } catch (error) {
    promoteMessage.value = { text: error?.message || "They could not be made an admin.", ok: false };
  } finally {
    promoting.value = false;
  }
}

async function revoke(email) {
  if (revokeConfirm.value !== email) {
    revokeConfirm.value = email;
    return;
  }
  revokeConfirm.value = "";
  try {
    await api.adminDemoteUser(email);
    await loadUsers();
  } catch (error) {
    usersError.value = error?.message || "Admin could not be removed.";
  }
}

function prefill(user) {
  promoteEmail.value = user.email || "";
  promoteUserId.value = user.user_id || "";
  promoteMessage.value = { text: "", ok: false };
  document.getElementById("adm-email")?.focus();
}

const myEmail = computed(() => state.auth?.connectedUser?.email || "");
const shownUsers = computed(() =>
  users.value.filter((user) =>
    matches(userSearch.value, [user.email, user.organization_id, user.user_id, user.profile?.full_name]),
  ),
);

// ── Sessions ──────────────────────────────────────────────────────────────
const sessions = ref([]);
const loadingSessions = ref(false);
const sessionsError = ref("");
const sessionSearch = ref("");
const deleteConfirm = ref("");
const deleting = ref("");

async function loadSessions() {
  loadingSessions.value = true;
  sessionsError.value = "";
  try {
    const data = await api.adminGetSessions();
    sessions.value = data?.sessions || [];
  } catch (error) {
    sessionsError.value = error?.message || "The sessions could not be loaded.";
  } finally {
    loadingSessions.value = false;
  }
}

async function deleteSession(sessionId) {
  if (deleteConfirm.value !== sessionId) {
    deleteConfirm.value = sessionId;
    return;
  }
  deleteConfirm.value = "";
  deleting.value = sessionId;
  try {
    await api.adminDeleteSession(sessionId);
    sessions.value = sessions.value.filter((session) => session.session_id !== sessionId);
  } catch (error) {
    sessionsError.value = error?.message || "The session could not be deleted.";
  } finally {
    deleting.value = "";
  }
}

const shownSessions = computed(() =>
  sessions.value.filter((session) =>
    matches(sessionSearch.value, [session.session_id, session.event_title, session.created_by_email, session.organization_id]),
  ),
);

// ── Organizations: each one's switches (Surprise me) ─────────────────────
const organizations = ref([]);
const loadingOrganizations = ref(false);
const organizationsError = ref("");
const savingOrganization = ref("");

async function loadOrganizations() {
  loadingOrganizations.value = true;
  organizationsError.value = "";
  try {
    const data = await api.adminGetOrganizations();
    organizations.value = data?.organizations || [];
  } catch (error) {
    organizationsError.value = error?.message || "The organizations could not be loaded.";
  } finally {
    loadingOrganizations.value = false;
  }
}

async function setSurprise(organization, enabled) {
  savingOrganization.value = organization.organizationId;
  organizationsError.value = "";
  try {
    await api.adminSetSurprise(organization.organizationId, enabled);
    organizations.value = organizations.value.map((item) =>
      item.organizationId === organization.organizationId
        ? { ...item, surpriseEnabled: enabled, updatedAt: new Date().toISOString(), updatedBy: state.auth.connectedUser?.email || item.updatedBy }
        : item,
    );
  } catch (error) {
    organizationsError.value = error?.message || "The setting could not be saved.";
  } finally {
    savingOrganization.value = "";
  }
}

// ── Models ────────────────────────────────────────────────────────────────
const settings = ref({});
const loadingSettings = ref(false);
const savingKey = ref(null);
const settingsMessage = ref({ text: "", ok: false });

// The server refuses a model id outside its catalog (livestorm_app/ai/models.py,
// checked by ai.jobs.check_model_setting), so every id here must be a
// classic-output model there; tests/test_model_catalog.py checks it.
// Claude 4.7 and later models take no `temperature`; llm_client sends it
// only to the models that accept it (anthropic_accepts_temperature).
// Models that think by default (Claude 5 and later) are not offered yet:
// thinking counts toward max_tokens, and the flows' token budgets are sized
// for models that answer directly.
const ANTHROPIC_TEXT_MODELS = [
  { value: "claude-haiku-4-5-20251001", label: "Claude Haiku 4.5 — fast & cheap" },
  { value: "claude-sonnet-4-6", label: "Claude Sonnet 4.6 — balanced" },
  { value: "claude-opus-4-8", label: "Claude Opus 4.8 — most capable" },
];

// gpt-image-1 was shut down on 2026-10-23. A saved gpt-image-1 setting is
// sent as gpt-image-2 (cover_image.resolve_image_model), so show that.
const IMAGE_MODELS = [{ value: "gpt-image-2", label: "gpt-image-2 (recommended)" }];
// Surprise me's voice (OpenAI gpt-4o-mini-tts): the server refuses any other
// (livestorm_app/ai/surprise_voice.py TTS_VOICES) and reads marin when unset.
const TTS_VOICES = ["alloy", "ash", "ballad", "cedar", "coral", "echo", "fable", "marin", "nova", "onyx", "sage", "shimmer", "verse"];
const activeVoice = computed(() => (TTS_VOICES.includes(settings.value.tts_voice) ? settings.value.tts_voice : "marin"));
const activeImageModel = computed(() => {
  const saved = settings.value.image_model;
  return IMAGE_MODELS.some((model) => model.value === saved) ? saved : "gpt-image-2";
});

// Every text job runs on Claude (the owner's decision, 2026-11-11): there is
// no provider to choose. A model saved before then that isn't a Claude model
// shows as unset; the server skips it and uses the job's default.
const textModels = ANTHROPIC_TEXT_MODELS;
function savedModel(key) {
  const saved = settings.value[key] || "";
  return textModels.some((model) => model.value === saved) ? saved : "";
}

async function loadSettings() {
  loadingSettings.value = true;
  settingsMessage.value = { text: "", ok: false };
  try {
    const data = await api.adminGetSettings();
    settings.value = data?.settings || {};
  } catch (error) {
    settingsMessage.value = { text: error?.message || "The settings could not be loaded.", ok: false };
  } finally {
    loadingSettings.value = false;
  }
}

async function saveSetting(key, value) {
  savingKey.value = key;
  settingsMessage.value = { text: "", ok: false };
  try {
    await api.adminUpdateSetting(key, value);
    settings.value = { ...settings.value, [key]: value };
    settingsMessage.value = { text: "Saved.", ok: true };
  } catch (error) {
    settingsMessage.value = { text: error?.message || "The setting could not be saved.", ok: false };
  } finally {
    savingKey.value = null;
  }
}

function switchTab(tab) {
  activeTab.value = tab;
  if (tab === "users" && !users.value.length) loadUsers();
  if (tab === "sessions" && !sessions.value.length) loadSessions();
  if (tab === "organizations" && !organizations.value.length) loadOrganizations();
  if (tab === "models" && !Object.keys(settings.value).length) loadSettings();
}

onMounted(() => {
  loadUsers();
  loadSettings();
});
</script>

<template>
  <div class="adm-page">
    <header class="adm-head">
      <h1 class="adm-title">Admin</h1>
      <p class="adm-lead">People, sessions, organizations' switches and AI models, across every organization. Only admins see this page.</p>
    </header>

    <div class="adm-tabs" role="group" aria-label="Admin sections">
      <button
        v-for="tab in TABS"
        :key="tab.value"
        type="button"
        class="adm-tab siq-fx"
        :class="{ 'is-current': activeTab === tab.value }"
        :aria-pressed="activeTab === tab.value ? 'true' : 'false'"
        @click="switchTab(tab.value)"
      >
        {{ tab.label }}
      </button>
    </div>

    <!-- ── People ── -->
    <template v-if="activeTab === 'users'">
      <Card>
        <CardHeader icon="shield" title="Make someone an admin" subtitle="They see this page and every organization's sessions." :level="2" />
        <form class="adm-form" @submit.prevent="promoteUser">
          <label class="adm-field">
            <span class="adm-label">Email</span>
            <input id="adm-email" v-model="promoteEmail" type="email" class="adm-input" autocomplete="off" required />
          </label>
          <label class="adm-field">
            <span class="adm-label">Livestorm user ID <span class="adm-muted">(optional)</span></span>
            <input v-model="promoteUserId" type="text" class="adm-input" autocomplete="off" spellcheck="false" />
          </label>
          <UiButton type="submit" variant="primary" icon="plus" :disabled="promoting || !promoteEmail.trim()">
            {{ promoting ? "Saving…" : "Make admin" }}
          </UiButton>
        </form>
        <p v-if="promoteMessage.text" class="adm-message" :role="promoteMessage.ok ? 'status' : 'alert'">
          <Icon :name="promoteMessage.ok ? 'check' : 'info'" :size="16" />{{ promoteMessage.text }}
        </p>
      </Card>

      <Card padding="flush">
        <div class="adm-bar" role="search">
          <label class="adm-field adm-search">
            <span class="siq-sr-only">Search people</span>
            <Icon name="search" :size="16" class="adm-field-icon" />
            <input v-model="userSearch" type="search" class="adm-input" placeholder="Search by email, name, organization or ID" />
          </label>
          <p class="adm-muted" role="status">{{ shownUsers.length }} {{ shownUsers.length === 1 ? "person" : "people" }}</p>
        </div>
        <p v-if="usersError" class="adm-message adm-pad" role="alert"><Icon name="info" :size="16" />{{ usersError }}</p>
        <p v-if="loadingUsers && !users.length" class="adm-muted adm-pad" role="status">Loading the people…</p>
        <div v-else-if="shownUsers.length" class="adm-table-wrap">
          <table class="adm-table">
            <caption class="siq-sr-only">People with access to StormIQ</caption>
            <thead>
              <tr>
                <th scope="col">Email</th>
                <th scope="col">Organization</th>
                <th scope="col" class="adm-num">Sessions</th>
                <th scope="col">Last seen</th>
                <th scope="col">Role</th>
                <th scope="col"><span class="siq-sr-only">Actions</span></th>
              </tr>
            </thead>
            <tbody>
              <tr v-for="user in shownUsers" :key="user.email || user.user_id">
                <th scope="row" class="adm-strong">
                  {{ user.email || "—" }}
                  <span v-if="user.user_id" class="adm-id">{{ user.user_id }}</span>
                </th>
                <td class="adm-id-cell">{{ user.organization_id || "—" }}</td>
                <td class="adm-num">{{ user.session_count ?? "—" }}</td>
                <td class="adm-date">{{ formatDate(user.updated_at) }}</td>
                <td>
                  <Chip v-if="user.is_admin" tone="accent" icon="shield">Admin</Chip>
                  <span v-else class="adm-muted">Member</span>
                </td>
                <td class="adm-actions">
                  <template v-if="user.is_admin && user.email !== myEmail">
                    <UiButton
                      variant="ghost"
                      small
                      :aria-label="revokeConfirm === user.email ? `Confirm: remove admin from ${user.email}` : `Remove admin from ${user.email}`"
                      @click="revoke(user.email)"
                    >
                      {{ revokeConfirm === user.email ? "Confirm" : "Remove admin" }}
                    </UiButton>
                    <UiButton v-if="revokeConfirm === user.email" variant="ghost" small @click="revokeConfirm = ''">Cancel</UiButton>
                  </template>
                  <span v-else-if="user.is_admin" class="adm-muted">You</span>
                  <UiButton v-else variant="ghost" small :aria-label="`Make ${user.email} an admin`" @click="prefill(user)">
                    Make admin
                  </UiButton>
                </td>
              </tr>
            </tbody>
          </table>
        </div>
        <p v-else class="adm-muted adm-pad">Nobody matches.</p>
      </Card>
    </template>

    <!-- ── Sessions ── -->
    <Card v-else-if="activeTab === 'sessions'" padding="flush">
      <div class="adm-bar" role="search">
        <label class="adm-field adm-search">
          <span class="siq-sr-only">Search sessions</span>
          <Icon name="search" :size="16" class="adm-field-icon" />
          <input v-model="sessionSearch" type="search" class="adm-input" placeholder="Search by title, ID, organization or who added it" />
        </label>
        <p class="adm-muted" role="status">{{ shownSessions.length }} {{ shownSessions.length === 1 ? "session" : "sessions" }}</p>
      </div>
      <p v-if="sessionsError" class="adm-message adm-pad" role="alert"><Icon name="info" :size="16" />{{ sessionsError }}</p>
      <p v-if="loadingSessions && !sessions.length" class="adm-muted adm-pad" role="status">Loading the sessions…</p>
      <div v-else-if="shownSessions.length" class="adm-table-wrap">
        <table class="adm-table">
          <caption class="siq-sr-only">Every organization's sessions</caption>
          <thead>
            <tr>
              <th scope="col">Session</th>
              <th scope="col">Organization</th>
              <th scope="col">Added by</th>
              <th scope="col">Updated</th>
              <th scope="col"><span class="siq-sr-only">Actions</span></th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="session in shownSessions" :key="session.session_id">
              <th scope="row" class="adm-strong">
                <RouterLink class="adm-link siq-fx" :to="`/sessions/${encodeURIComponent(session.session_id)}/report`">
                  {{ session.event_title || "Untitled session" }}
                </RouterLink>
                <span class="adm-id">{{ session.session_id }}</span>
              </th>
              <td class="adm-id-cell">{{ session.organization_id || "—" }}</td>
              <td>{{ session.created_by_email || "—" }}</td>
              <td class="adm-date">{{ formatDate(session.updated_at) }}</td>
              <td class="adm-actions">
                <UiButton
                  variant="ghost"
                  small
                  :icon="deleteConfirm === session.session_id ? '' : 'x'"
                  :disabled="deleting === session.session_id"
                  :aria-label="
                    deleteConfirm === session.session_id
                      ? `Confirm: delete ${session.event_title || session.session_id} for good`
                      : `Delete ${session.event_title || session.session_id}`
                  "
                  @click="deleteSession(session.session_id)"
                >
                  {{ deleting === session.session_id ? "Deleting…" : deleteConfirm === session.session_id ? "Delete for good" : "Delete" }}
                </UiButton>
                <UiButton v-if="deleteConfirm === session.session_id" variant="ghost" small @click="deleteConfirm = ''">Cancel</UiButton>
              </td>
            </tr>
          </tbody>
        </table>
      </div>
      <p v-else class="adm-muted adm-pad">No session matches.</p>
      <p class="adm-muted adm-pad adm-note">Deleting removes the session's saved data, transcript and outputs from StormIQ; it doesn't touch Livestorm.</p>
    </Card>

    <!-- ── Organizations ── -->
    <Card v-else-if="activeTab === 'organizations'" padding="flush">
      <div class="adm-bar">
        <p class="adm-muted">
          Surprise me is off for every organization except StormIQ's own (the one an admin signs in from) until you
          turn it on here. Members can't change it.
        </p>
      </div>
      <p v-if="organizationsError" class="adm-message adm-pad" role="alert"><Icon name="info" :size="16" />{{ organizationsError }}</p>
      <p v-if="loadingOrganizations && !organizations.length" class="adm-muted adm-pad" role="status">Loading the organizations…</p>
      <div v-else-if="organizations.length" class="adm-table-wrap">
        <table class="adm-table">
          <caption class="siq-sr-only">Every organization and its switches</caption>
          <thead>
            <tr>
              <th scope="col">Organization</th>
              <th scope="col" class="adm-num">Sessions</th>
              <th scope="col">Surprise me</th>
              <th scope="col">Changed</th>
              <th scope="col"><span class="siq-sr-only">Actions</span></th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="organization in organizations" :key="organization.organizationId">
              <th scope="row" class="adm-id-cell">{{ organization.organizationId }}</th>
              <td class="adm-num">{{ organization.sessions }}</td>
              <td>
                <StatusWord v-if="organization.surpriseEnabled" status="measured" label="On" />
                <StatusWord v-else status="check" label="Off" />
              </td>
              <td class="adm-date">
                {{ organization.updatedAt ? formatDate(organization.updatedAt) : "—" }}
                <span v-if="organization.updatedBy" class="adm-id">{{ organization.updatedBy }}</span>
              </td>
              <td class="adm-actions">
                <UiButton
                  variant="ghost"
                  small
                  :disabled="savingOrganization === organization.organizationId"
                  :aria-label="`${organization.surpriseEnabled ? 'Turn off' : 'Turn on'} Surprise me for ${organization.organizationId}`"
                  @click="setSurprise(organization, !organization.surpriseEnabled)"
                >
                  {{ savingOrganization === organization.organizationId ? "Saving…" : organization.surpriseEnabled ? "Turn off" : "Turn on" }}
                </UiButton>
              </td>
            </tr>
          </tbody>
        </table>
      </div>
      <p v-else class="adm-muted adm-pad">No organization has a session yet.</p>
    </Card>

    <!-- ── Models ── -->
    <template v-else>
      <p v-if="loadingSettings" class="adm-muted" role="status">Loading the settings…</p>
      <template v-else>
        <p v-if="settingsMessage.text" class="adm-message" :role="settingsMessage.ok ? 'status' : 'alert'">
          <Icon :name="settingsMessage.ok ? 'check' : 'info'" :size="16" />{{ settingsMessage.text }}
        </p>
        <div class="adm-models">
          <Card>
            <CardHeader icon="file-text" title="Default model" subtitle="Claude. Analyses, repurposed content and translations." :level="2" />
            <label class="adm-field adm-select-field">
              <span class="siq-sr-only">Default model</span>
              <select
                class="adm-input adm-select"
                :value="savedModel('llm_default_model')"
                :disabled="savingKey !== null"
                @change="saveSetting('llm_default_model', $event.target.value)"
              >
                <option value="" disabled>Choose a model</option>
                <option v-for="model in textModels" :key="model.value" :value="model.value">{{ model.label }}</option>
              </select>
              <Icon name="chevron-down" :size="16" class="adm-select-icon" />
            </label>
          </Card>

          <Card>
            <CardHeader icon="star" title="Smart Recap model" subtitle="The Smart Recap only: a stronger model for the narrative." :level="2" />
            <label class="adm-field adm-select-field">
              <span class="siq-sr-only">Smart Recap model</span>
              <select
                class="adm-input adm-select"
                :value="savedModel('llm_smart_recap_model')"
                :disabled="savingKey !== null"
                @change="saveSetting('llm_smart_recap_model', $event.target.value)"
              >
                <option value="" disabled>Choose a model</option>
                <option v-for="model in textModels" :key="model.value" :value="model.value">{{ model.label }}</option>
              </select>
              <Icon name="chevron-down" :size="16" class="adm-select-icon" />
            </label>
          </Card>

          <Card>
            <CardHeader icon="presentation" title="Cover image model" subtitle="OpenAI Images, for the session covers." :level="2" />
            <p class="adm-text adm-after">{{ IMAGE_MODELS.find((model) => model.value === activeImageModel)?.label || activeImageModel }}</p>
            <UiButton
              v-if="settings.image_model && settings.image_model !== activeImageModel"
              variant="secondary"
              small
              :disabled="savingKey !== null"
              @click="saveSetting('image_model', activeImageModel)"
            >
              Save {{ activeImageModel }}
            </UiButton>
          </Card>

          <Card>
            <CardHeader icon="play" title="Surprise me voice" subtitle="OpenAI's gpt-4o-mini-tts reads the take aloud. A new voice records again on the next play." :level="2" />
            <label class="adm-field adm-select-field">
              <span class="siq-sr-only">Surprise me voice</span>
              <select
                class="adm-input adm-select"
                :value="activeVoice"
                :disabled="savingKey !== null"
                @change="saveSetting('tts_voice', $event.target.value)"
              >
                <option v-for="name in TTS_VOICES" :key="name" :value="name">{{ name }}{{ name === "marin" ? " (default)" : "" }}</option>
              </select>
              <Icon name="chevron-down" :size="16" class="adm-select-icon" />
            </label>
          </Card>
        </div>
      </template>
    </template>
  </div>
</template>

<style scoped>
.adm-page {
  display: flex;
  flex-direction: column;
  gap: var(--siq-space-6);
}

.adm-head {
  display: flex;
  flex-direction: column;
  gap: var(--siq-space-2);
}

.adm-title {
  margin: 0;
  font-size: var(--siq-text-display);
  line-height: var(--siq-text-display-lh);
  font-weight: var(--siq-weight-regular);
  letter-spacing: -0.015em;
  color: var(--siq-ink);
}

.adm-lead,
.adm-text {
  margin: 0;
  font-size: var(--siq-text-ui);
  line-height: var(--siq-text-ui-lh);
  color: var(--siq-ink-2);
}

.adm-muted {
  margin: 0;
  font-size: var(--siq-text-small);
  line-height: var(--siq-text-small-lh);
  font-weight: var(--siq-weight-regular);
  color: var(--siq-muted);
}

.adm-pad {
  padding: var(--siq-space-4) var(--siq-space-6);
}

.adm-note {
  box-shadow: inset 0 1px 0 var(--siq-hairline);
}

.adm-after {
  margin-top: var(--siq-space-3);
}

.adm-message {
  display: flex;
  align-items: center;
  gap: var(--siq-space-2);
  margin: var(--siq-space-3) 0 0;
  font-size: var(--siq-text-small);
  line-height: var(--siq-text-small-lh);
  color: var(--siq-ink-2);
}

/* ── The section switch ── */
.adm-tabs {
  display: flex;
  gap: var(--siq-space-2);
  box-shadow: inset 0 -1px 0 var(--siq-hairline);
  overflow-x: auto;
}

.adm-tab {
  min-height: var(--siq-target);
  margin: 0;
  padding: 0 var(--siq-space-3);
  border: 0;
  border-radius: 0;
  background: transparent;
  color: var(--siq-ink-2);
  font: inherit;
  font-size: var(--siq-text-nav);
  line-height: var(--siq-text-nav-lh);
  font-weight: var(--siq-weight-medium);
  cursor: pointer;
  transition-property: color, box-shadow;
  transition-duration: var(--siq-duration);
  transition-timing-function: var(--siq-ease);
}

.adm-tab:hover {
  color: var(--siq-ink);
}

.adm-tab.is-current {
  color: var(--siq-ink);
  box-shadow: inset 0 -2px 0 var(--siq-accent);
}

.adm-tab.siq-fx:focus-visible {
  outline-offset: -3px;
}

/* ── Forms ── */
.adm-form {
  display: flex;
  flex-wrap: wrap;
  align-items: flex-end;
  gap: var(--siq-space-3);
  margin-top: var(--siq-space-4);
}

.adm-field {
  position: relative;
  display: flex;
  flex-direction: column;
  gap: var(--siq-space-1);
  flex: 1 1 16rem;
}

.adm-label {
  font-size: var(--siq-text-small);
  line-height: var(--siq-text-small-lh);
  font-weight: var(--siq-weight-medium);
  color: var(--siq-ink-2);
}

/* v1 controls set their own font, colours, border and height: the classic
   element styles still load under v1. */
.adm-input {
  width: 100%;
  height: var(--siq-target);
  margin: 0;
  padding: 0 var(--siq-space-3);
  border: 1px solid var(--siq-border);
  border-radius: var(--siq-radius-control);
  background: var(--siq-input);
  color: var(--siq-ink);
  font: inherit;
  font-size: var(--siq-text-ui);
  line-height: var(--siq-text-ui-lh);
  box-shadow: none;
  transition-property: border-color;
  transition-duration: var(--siq-duration);
  transition-timing-function: var(--siq-ease);
}

.adm-input:hover {
  border-color: var(--siq-border-hover);
}

.adm-input:focus-visible {
  outline: 2px solid var(--siq-focus);
  outline-offset: 1px;
}

.adm-input::placeholder {
  color: var(--siq-muted);
}

.adm-search {
  flex-direction: row;
  align-items: center;
}

.adm-search .adm-input {
  padding-left: 36px;
}

.adm-field-icon,
.adm-select-icon {
  position: absolute;
  color: var(--siq-muted);
  pointer-events: none;
}

.adm-field-icon {
  left: var(--siq-space-3);
}

.adm-select-field {
  flex-direction: row;
  align-items: center;
  margin-top: var(--siq-space-4);
}

.adm-select {
  appearance: none;
  padding-right: 36px;
  cursor: pointer;
}

.adm-select-icon {
  right: var(--siq-space-3);
}

.adm-bar {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: var(--siq-space-3);
  padding: var(--siq-space-4) var(--siq-space-6);
  box-shadow: inset 0 -1px 0 var(--siq-hairline);
}

/* ── Tables ── */
.adm-table-wrap {
  /* Positioned, so the table's screen-reader labels (absolute) scroll with
     it instead of widening the page on a phone. */
  position: relative;
  overflow-x: auto;
}

.adm-table {
  width: 100%;
  border-collapse: collapse;
  font-size: var(--siq-text-ui);
  line-height: var(--siq-text-ui-lh);
  color: var(--siq-ink);
}

.adm-table th,
.adm-table td {
  padding: var(--siq-space-3) var(--siq-space-4);
  text-align: left;
  vertical-align: middle;
  box-shadow: inset 0 -1px 0 var(--siq-hairline-row);
}

.adm-table th:first-child,
.adm-table td:first-child {
  padding-left: var(--siq-space-6);
}

.adm-table th:last-child,
.adm-table td:last-child {
  padding-right: var(--siq-space-6);
}

.adm-table thead th {
  font-size: var(--siq-text-small);
  line-height: var(--siq-text-small-lh);
  font-weight: var(--siq-weight-medium);
  color: var(--siq-muted);
  box-shadow: inset 0 -1px 0 var(--siq-hairline-head);
}

.adm-table tbody tr:last-child th,
.adm-table tbody tr:last-child td {
  box-shadow: none;
}

.adm-strong {
  font-weight: var(--siq-weight-medium);
  min-width: 14rem;
}

.adm-id {
  display: block;
  font-size: var(--siq-text-small);
  line-height: var(--siq-text-small-lh);
  font-weight: var(--siq-weight-regular);
  color: var(--siq-muted);
  font-variant-numeric: tabular-nums;
  overflow-wrap: anywhere;
}

.adm-id-cell {
  font-size: var(--siq-text-small);
  color: var(--siq-ink-2);
  overflow-wrap: anywhere;
  max-width: 14rem;
}

.adm-table .adm-num {
  text-align: right;
  font-variant-numeric: tabular-nums;
}

.adm-date {
  white-space: nowrap;
  color: var(--siq-ink-2);
}

.adm-table .adm-actions {
  white-space: nowrap;
  text-align: right;
}

.adm-link {
  color: var(--siq-ink);
  text-decoration: none;
  border-radius: var(--siq-radius-inline);
}

.adm-link:hover {
  color: var(--siq-accent);
}

/* ── Models ── */
.adm-models {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: var(--siq-space-4);
}

@media (max-width: 720px) {
  .adm-title {
    font-size: var(--siq-text-headline);
    line-height: var(--siq-text-headline-lh);
  }
  .adm-models {
    grid-template-columns: minmax(0, 1fr);
  }
  .adm-bar,
  .adm-pad {
    padding-left: var(--siq-space-4);
    padding-right: var(--siq-space-4);
  }
}

@media (forced-colors: active) {
  .adm-tab.is-current {
    outline: 2px solid CanvasText;
  }
  .adm-table th,
  .adm-table td {
    border-bottom: 1px solid CanvasText;
  }
}

@media (prefers-reduced-motion: reduce) {
  .adm-tab,
  .adm-input {
    transition: none;
  }
}
</style>
