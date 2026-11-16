import { useCallback, useEffect, useMemo, useRef, useState, type FormEvent } from 'react';
import { api, type Item } from '../../api';
import type { ColumnDef, ColumnFiltersState, Updater } from '@tanstack/react-table';
import { BLANK_FILTER_VALUE } from '../../components/DataTable';
import { OperationsPanel } from './OperationsPanel';
import { UsersPanel } from './UsersPanel';
import { AuditPanel, ItemsPanel, NumberingPanel } from './DataPanels';

import '../Admin.css';

import {
  type ApprovalGroup,
  type AuditEntry,
  type CadWorkerSettings,
  type CadWorkerStatus,
  type CheckoutNotificationRun,
  type ItemNumberTemplate,
  type ItemPurgeDialog,
  type ItemPurgePreview,
  type RevisionDeletionDialog,
  type RevisionDeletionOption,
  type Status,
  type User,
  type UserActivity,
} from './types';

const roles = ['reader', 'designer', 'reviewer', 'administrator'];
const formatBytes = (bytes: number) => `${(bytes / 1024 / 1024 / 1024).toFixed(1)} GB`;
const isUserLocked = (user: User) =>
  !!user.locked_until && new Date(user.locked_until).getTime() > Date.now();
const adminTabs = ['operations', 'users', 'items', 'numbering', 'audit'] as const;
type AdminTab = (typeof adminTabs)[number];

function tabFromHash(): AdminTab {
  const value = window.location.hash.slice(1);
  return adminTabs.includes(value as AdminTab) ? (value as AdminTab) : 'operations';
}

export function AdminConsole({
  isAdmin,
  currentUsername,
}: {
  isAdmin: boolean;
  currentUsername: string;
}) {
  const [users, setUsers] = useState<User[]>([]);
  const [approvalGroups, setApprovalGroups] = useState<ApprovalGroup[]>([]);
  const [items, setItems] = useState<Item[]>([]);
  const [status, setStatus] = useState<Status | null>(null);
  const [cadWorkerSettings, setCadWorkerSettings] = useState<CadWorkerSettings>({
    freecad_url: null,
    freecad_analyzer_url: null,
  });
  const [cadWorkerStatus, setCadWorkerStatus] = useState<CadWorkerStatus | null>(null);
  const [cadWorkerSettingsBusy, setCadWorkerSettingsBusy] = useState(false);
  const [cadWorkerStatusBusy, setCadWorkerStatusBusy] = useState(false);
  const [cadWorkerSettingsSaved, setCadWorkerSettingsSaved] = useState(false);
  const [notificationRun, setNotificationRun] = useState<CheckoutNotificationRun | null>(null);
  const [notificationBusy, setNotificationBusy] = useState(false);
  const [audit, setAudit] = useState<AuditEntry[]>([]);
  const [numberTemplates, setNumberTemplates] = useState<ItemNumberTemplate[]>([]);
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  const [activeTab, setActiveTab] = useState<AdminTab>(tabFromHash);
  const [passwordUser, setPasswordUser] = useState<User | null>(null);
  const [selectedUser, setSelectedUser] = useState<User | null>(null);
  const [showCreateUser, setShowCreateUser] = useState(false);
  const [userSearch, setUserSearch] = useState('');
  const [userRoleFilter, setUserRoleFilter] = useState('');
  const [userStatusFilter, setUserStatusFilter] = useState('');
  const [userGroupFilter, setUserGroupFilter] = useState('');
  const [userActivity, setUserActivity] = useState<UserActivity[]>([]);
  const [userActivityLoading, setUserActivityLoading] = useState(false);
  const [userActivityError, setUserActivityError] = useState('');
  const [editingItem, setEditingItem] = useState<Item | null>(null);
  const [purgeDialog, setPurgeDialog] = useState<ItemPurgeDialog | null>(null);
  const [purgeError, setPurgeError] = useState('');
  const [revisionDeletionDialog, setRevisionDeletionDialog] =
    useState<RevisionDeletionDialog | null>(null);
  const [revisionDeletionError, setRevisionDeletionError] = useState('');
  const [itemNumberFilter, setItemNumberFilter] = useState('');
  const [itemNameFilter, setItemNameFilter] = useState('');
  const [itemFilters, setItemFilters] = useState<ColumnFiltersState>([]);
  const [itemFacets, setItemFacets] = useState<Record<string, string[]>>({});
  const [itemHeaderFiltersAvailable, setItemHeaderFiltersAvailable] = useState(false);
  const itemRequest = useRef(0);
  const [appliedItemSearch, setAppliedItemSearch] = useState({ number: '', name: '' });
  const [itemTotal, setItemTotal] = useState(0);
  const [itemOffset, setItemOffset] = useState(0);
  const [showAddNumberTemplate, setShowAddNumberTemplate] = useState(false);

  // per-tab loading + loaded flags
  const [loadingOps, setLoadingOps] = useState(false);
  const [loadingUsers, setLoadingUsers] = useState(false);
  const [loadingItems, setLoadingItems] = useState(false);
  const [loadingAudit, setLoadingAudit] = useState(false);
  const [loadingNumbering, setLoadingNumbering] = useState(false);

  const [loadedOps, setLoadedOps] = useState(false);
  const [loadedUsers, setLoadedUsers] = useState(false);
  const [loadedItems, setLoadedItems] = useState(false);
  const [loadedAudit, setLoadedAudit] = useState(false);
  const [loadedNumbering, setLoadedNumbering] = useState(false);

  const refreshCadWorkerStatus = useCallback(async () => {
    setCadWorkerStatus(await api<CadWorkerStatus>('/admin/cad-workers/status'));
  }, []);

  const checkCadWorkerStatus = async () => {
    setCadWorkerStatusBusy(true);
    setError('');
    try {
      await refreshCadWorkerStatus();
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : 'Unable to check CAD server status');
    } finally {
      setCadWorkerStatusBusy(false);
    }
  };

  const loadOperations = useCallback(async () => {
    setError('');
    setLoadingOps(true);
    try {
      const [operational, workerSettings, workerStatus] = await Promise.all([
        api<Status>('/operations/status'),
        api<CadWorkerSettings>('/admin/cad-workers'),
        api<CadWorkerStatus>('/admin/cad-workers/status'),
      ]);
      setStatus(operational);
      setCadWorkerSettings(workerSettings);
      setCadWorkerStatus(workerStatus);
      setLoadedOps(true);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : 'Unable to load operations');
    } finally {
      setLoadingOps(false);
    }
  }, []);

  const saveCadWorkerSettings = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    const data = new FormData(event.currentTarget);
    setCadWorkerSettingsBusy(true);
    setCadWorkerSettingsSaved(false);
    setError('');
    try {
      const saved = await api<CadWorkerSettings>('/admin/cad-workers', 'PUT', {
        freecad_url: String(data.get('freecadUrl') ?? '').trim() || null,
        freecad_analyzer_url: String(data.get('freecadAnalyzerUrl') ?? '').trim() || null,
      });
      setCadWorkerSettings(saved);
      setCadWorkerSettingsSaved(true);
      await refreshCadWorkerStatus();
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : 'Unable to save CAD server settings');
    } finally {
      setCadWorkerSettingsBusy(false);
    }
  };

  useEffect(() => {
    if (!isAdmin || activeTab !== 'operations') return undefined;
    const timer = window.setInterval(() => {
      void refreshCadWorkerStatus().catch(() => undefined);
    }, 15_000);
    return () => window.clearInterval(timer);
  }, [activeTab, isAdmin, refreshCadWorkerStatus]);

  const runCheckoutNotifications = async () => {
    setNotificationBusy(true);
    setError('');
    try {
      const result = await api<CheckoutNotificationRun>(
        '/admin/maintenance/checkout-notifications/run',
        'POST'
      );
      setNotificationRun(result);
      await loadOperations();
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : 'Unable to generate notifications');
    } finally {
      setNotificationBusy(false);
    }
  };

  const loadUsers = useCallback(async () => {
    setError('');
    setLoadingUsers(true);
    try {
      const [userRows, groupRows] = await Promise.all([
        api<User[]>('/admin/users'),
        api<ApprovalGroup[]>('/admin/approval-groups'),
      ]);
      setUsers(userRows);
      setApprovalGroups(groupRows);
      setLoadedUsers(true);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : 'Unable to load users');
    } finally {
      setLoadingUsers(false);
    }
  }, []);

  const loadItems = useCallback(
    async (offset = itemOffset, filters = itemFilters, search = appliedItemSearch) => {
      const requestId = ++itemRequest.current;
      setError('');
      setLoadingItems(true);
      try {
        const query = new URLSearchParams({
          include_incomplete: 'true',
          include_archived: 'true',
          include_facets: 'true',
          limit: '25',
          offset: String(offset),
        });
        if (search.number.trim()) query.set('item_number_regex', search.number.trim());
        if (search.name.trim()) query.set('name_regex', search.name.trim());
        const fields: Record<string, string> = {
          type: 'item_types',
          cad_system: 'cad_systems',
          status: 'item_statuses',
        };
        for (const filter of filters) {
          const values = filter.value as string[];
          if (fields[filter.id] && Array.isArray(values)) {
            for (const value of values.length ? values : ['__modelryx_no_values__'])
              query.append(fields[filter.id], value === BLANK_FILTER_VALUE ? '' : value);
          }
        }
        const result = await api<{
          items: Item[];
          total: number;
          facets?: Record<string, (string | null)[]> | null;
        }>(`/items?${query}`);
        if (requestId !== itemRequest.current) return;
        setItems(result.items);
        setItemTotal(result.total);
        setItemOffset(offset);
        const facets = result.facets;
        const filtersAvailable =
          !!facets &&
          Array.isArray(facets.item_types) &&
          Array.isArray(facets.cad_systems) &&
          Array.isArray(facets.item_statuses);
        setItemHeaderFiltersAvailable(filtersAvailable);
        if (filtersAvailable && facets) {
          setItemFacets({
            type: facets.item_types.map((value) => value ?? BLANK_FILTER_VALUE),
            cad_system: facets.cad_systems.map((value) => value || BLANK_FILTER_VALUE),
            status: facets.item_statuses.map((value) => value ?? BLANK_FILTER_VALUE),
          });
        } else {
          setItemFacets({});
          if (filters.length) setItemFilters([]);
        }
        setLoadedItems(true);
      } catch (reason) {
        if (requestId === itemRequest.current) {
          setItems([]);
          setItemTotal(0);
          setError(reason instanceof Error ? reason.message : 'Unable to load items');
        }
      } finally {
        if (requestId === itemRequest.current) setLoadingItems(false);
      }
    },
    [itemOffset, itemFilters, appliedItemSearch]
  );
  const changeItemFilters = (updater: Updater<ColumnFiltersState>) => {
    const next = typeof updater === 'function' ? updater(itemFilters) : updater;
    setItemFilters(next);
    void loadItems(0, next);
  };
  const applyItemSearch = () => {
    const next = { number: itemNumberFilter, name: itemNameFilter };
    setAppliedItemSearch(next);
    return loadItems(0, itemFilters, next);
  };
  const clearItemFilters = () => {
    setItemNumberFilter('');
    setItemNameFilter('');
    setAppliedItemSearch({ number: '', name: '' });
    setItemFilters([]);
    void loadItems(0, [], { number: '', name: '' });
  };

  const loadAudit = useCallback(async () => {
    setError('');
    setLoadingAudit(true);
    try {
      const auditRows = await api<AuditEntry[]>('/operations/audit?limit=20');
      setAudit(auditRows);
      setLoadedAudit(true);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : 'Unable to load audit entries');
    } finally {
      setLoadingAudit(false);
    }
  }, []);

  const loadNumbering = useCallback(async () => {
    setError('');
    setLoadingNumbering(true);
    try {
      setNumberTemplates(await api<ItemNumberTemplate[]>('/admin/item-number-templates'));
      setLoadedNumbering(true);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : 'Unable to load Item-number templates');
    } finally {
      setLoadingNumbering(false);
    }
  }, []);

  useEffect(() => {
    const syncHash = () => setActiveTab(tabFromHash());
    window.addEventListener('hashchange', syncHash);
    return () => window.removeEventListener('hashchange', syncHash);
  }, []);

  useEffect(() => {
    if (!isAdmin) return;
    if (activeTab === 'operations' && !loadedOps) void loadOperations();
    if (activeTab === 'users' && !loadedUsers) void loadUsers();
    if (activeTab === 'items' && !loadedItems) void loadItems();
    if (activeTab === 'numbering' && !loadedNumbering) void loadNumbering();
    if (activeTab === 'audit' && !loadedAudit) void loadAudit();
  }, [
    activeTab,
    isAdmin,
    loadedAudit,
    loadedItems,
    loadedOps,
    loadedNumbering,
    loadedUsers,
    loadAudit,
    loadItems,
    loadOperations,
    loadNumbering,
    loadUsers,
  ]);

  useEffect(() => {
    if (!selectedUser) {
      setUserActivity([]);
      return;
    }
    let active = true;
    setUserActivityLoading(true);
    setUserActivityError('');
    void api<UserActivity[]>(`/admin/users/${selectedUser.id}/activity`)
      .then((activity) => {
        if (active) setUserActivity(activity);
      })
      .catch((reason) => {
        if (active) {
          setUserActivity([]);
          setUserActivityError(
            reason instanceof Error ? reason.message : 'Unable to load activity'
          );
        }
      })
      .finally(() => {
        if (active) setUserActivityLoading(false);
      });
    return () => {
      active = false;
    };
  }, [selectedUser]);

  const selectTab = (tab: AdminTab) => {
    window.location.hash = tab;
    setActiveTab(tab);
  };

  const createUser = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    if (busy) return;
    const form = event.currentTarget;
    const data = new FormData(form);
    setError('');
    setBusy(true);
    try {
      await api('/admin/users', 'POST', {
        username: String(data.get('username')),
        display_name: String(data.get('displayName')),
        roles: data.getAll('roles').map(String),
        password: String(data.get('password')),
      });
      form.reset();
      setShowCreateUser(false);
      await loadUsers();
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : 'Unable to create user');
    } finally {
      setBusy(false);
    }
  };

  const createNumberTemplate = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    if (busy) return;
    const form = event.currentTarget;
    const data = new FormData(form);
    setError('');
    setBusy(true);
    setError('');
    try {
      await api('/admin/item-number-templates', 'POST', {
        name: String(data.get('name')),
        part_pattern: String(data.get('partPattern')),
        assembly_pattern: String(data.get('assemblyPattern')),
        part_next_value: Number(data.get('partNextValue')),
        assembly_next_value: Number(data.get('assemblyNextValue')),
        is_selected: data.get('selected') === 'on',
      });
      form.reset();
      setShowAddNumberTemplate(false);
      await loadNumbering();
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : 'Unable to create Item-number template');
    } finally {
      setBusy(false);
    }
  };

  const updateNumberTemplate = async (
    template: ItemNumberTemplate,
    changes: Record<string, unknown>
  ) => {
    setError('');
    try {
      await api(`/admin/item-number-templates/${template.id}`, 'PATCH', changes);
      await loadNumbering();
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : 'Unable to update Item-number template');
    }
  };

  const editNumberTemplate = async (template: ItemNumberTemplate) => {
    const name = window.prompt('Template name', template.name);
    if (name === null) return;
    const partPattern = window.prompt('Part pattern', template.part_pattern);
    if (partPattern === null) return;
    const assemblyPattern = window.prompt('Assembly pattern', template.assembly_pattern);
    if (assemblyPattern === null) return;
    await updateNumberTemplate(template, {
      name,
      part_pattern: partPattern,
      assembly_pattern: assemblyPattern,
    });
  };

  const createApprovalGroup = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    if (busy) return;
    const form = event.currentTarget;
    const data = new FormData(form);
    setError('');
    setBusy(true);
    setError('');
    try {
      await api('/admin/approval-groups', 'POST', {
        name: String(data.get('groupName')),
        member_ids: data.getAll('memberIds').map(String),
      });
      form.reset();
      await loadUsers();
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : 'Unable to create reviewer group');
    } finally {
      setBusy(false);
    }
  };

  const toggleApprovalGroup = async (group: ApprovalGroup) => {
    setError('');
    try {
      await api(`/admin/approval-groups/${group.id}`, 'PATCH', {
        is_active: !group.is_active,
      });
      await loadUsers();
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : 'Unable to update reviewer group');
    }
  };

  const saveUserDetails = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    if (!selectedUser || busy) return;
    const data = new FormData(event.currentTarget);
    const nextRoles = data.getAll('roles').map(String);
    if (nextRoles.length === 0) {
      setError('At least one role is required.');
      return;
    }
    const groupEligible = nextRoles.some((role) => role === 'reviewer' || role === 'administrator');
    const selectedGroupIds = new Set(
      groupEligible ? data.getAll('approvalGroups').map(String) : []
    );
    setBusy(true);
    setError('');
    try {
      await api(`/admin/users/${selectedUser.id}`, 'PATCH', {
        display_name: String(data.get('displayName')),
        roles: nextRoles,
        is_active: data.get('isActive') === 'on',
      });
      for (const group of approvalGroups) {
        const currentlyMember = group.member_ids.includes(selectedUser.id);
        const shouldBeMember = selectedGroupIds.has(group.id);
        if (currentlyMember === shouldBeMember) continue;
        await api(`/admin/approval-groups/${group.id}`, 'PATCH', {
          member_ids: shouldBeMember
            ? [...group.member_ids, selectedUser.id]
            : group.member_ids.filter((id) => id !== selectedUser.id),
        });
      }
      setSelectedUser(null);
      await loadUsers();
    } catch (reason) {
      await loadUsers();
      setSelectedUser(null);
      setError(
        `Some changes may have been saved. Review the refreshed user and group details. ${reason instanceof Error ? reason.message : 'Unable to update user'}`
      );
    } finally {
      setBusy(false);
    }
  };

  const resetPassword = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    if (!passwordUser || busy) return;
    setBusy(true);
    setError('');
    const form = event.currentTarget;
    const password = String(new FormData(form).get('password'));
    try {
      await api(`/admin/users/${passwordUser.id}/password`, 'POST', { password });
      setPasswordUser(null);
      await loadUsers();
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : 'Unable to reset password');
    } finally {
      setBusy(false);
    }
  };

  const revokeUserSessions = async (user: User) => {
    if (!window.confirm(`Sign out ${user.display_name} from all current sessions?`)) return;
    setBusy(true);
    setError('');
    try {
      await api(`/admin/users/${user.id}/sessions/revoke`, 'POST');
      setSelectedUser(null);
      await loadUsers();
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : 'Unable to revoke sessions');
    } finally {
      setBusy(false);
    }
  };

  const unlockUser = async (user: User) => {
    setBusy(true);
    setError('');
    try {
      await api(`/admin/users/${user.id}/unlock`, 'POST');
      setSelectedUser(null);
      await loadUsers();
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : 'Unable to unlock account');
    } finally {
      setBusy(false);
    }
  };

  const toggleItem = async (item: Item) => {
    try {
      await api(`/items/${item.id}`, 'PATCH', {
        status: item.status === 'ARCHIVED' ? 'ACTIVE' : 'ARCHIVED',
      });
      await loadItems();
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : 'Unable to update item');
    }
  };

  const updateItem = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    if (!editingItem) return;
    const data = new FormData(event.currentTarget);
    setBusy(true);
    setError('');
    try {
      await api(`/items/${editingItem.id}`, 'PATCH', {
        name: String(data.get('name')),
        description: String(data.get('description')) || null,
        category: String(data.get('category')) || null,
      });
      setEditingItem(null);
      await loadItems();
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : 'Unable to update item');
    } finally {
      setBusy(false);
    }
  };

  const deleteItem = async (item: Item) => {
    if (
      !window.confirm(
        `Delete ${item.item_number} — ${item.name}? The item will be archived and can be restored later.`
      )
    )
      return;
    setError('');
    try {
      await api(`/items/${item.id}`, 'DELETE');
      await loadItems();
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : 'Unable to delete item');
    }
  };

  const deleteRevision = async (item: Item) => {
    setError('');
    setRevisionDeletionError('');
    try {
      const revisions = await api<RevisionDeletionOption[]>(
        `/admin/items/${item.id}/revisions/deletion-options`
      );
      setRevisionDeletionDialog({
        item,
        revisions,
        selectedRevisionId: revisions.find((revision) => revision.eligible)?.id ?? '',
        confirmation: '',
      });
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : 'Unable to load Item revisions');
    }
  };

  const confirmRevisionDeletion = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    if (!revisionDeletionDialog) return;
    const revision = revisionDeletionDialog.revisions.find(
      (candidate) => candidate.id === revisionDeletionDialog.selectedRevisionId
    );
    if (!revision?.eligible) {
      setRevisionDeletionError('Select a revision that can be deleted.');
      return;
    }
    if (revisionDeletionDialog.confirmation !== revision.revision_code) {
      setRevisionDeletionError('The revision code does not match.');
      return;
    }
    setBusy(true);
    setRevisionDeletionError('');
    try {
      await api(
        `/admin/items/${revisionDeletionDialog.item.id}/revisions/${revision.id}/delete`,
        'POST',
        { revision_code: revisionDeletionDialog.confirmation }
      );
      setRevisionDeletionDialog(null);
      await loadItems(itemOffset);
      setLoadedAudit(false);
    } catch (reason) {
      setRevisionDeletionError(
        reason instanceof Error ? reason.message : 'Unable to delete revision'
      );
    } finally {
      setBusy(false);
    }
  };

  const purgeItem = async (item: Item) => {
    setError('');
    setPurgeError('');
    try {
      const preview = await api<ItemPurgePreview>(`/admin/items/${item.id}/purge`);
      setPurgeDialog({ item, preview, selectedItemNumbers: [], confirmation: '' });
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : 'Unable to permanently delete item');
    }
  };

  const confirmPurge = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    if (!purgeDialog) return;
    if (purgeDialog.confirmation !== purgeDialog.item.item_number) {
      setPurgeError('The item number does not match. Enter the exact item number to continue.');
      return;
    }
    setBusy(true);
    setPurgeError('');
    try {
      await api(`/admin/items/${purgeDialog.item.id}/purge`, 'POST', {
        item_number: purgeDialog.confirmation,
        cascade_item_numbers: purgeDialog.selectedItemNumbers,
      });
      setPurgeDialog(null);
      await loadItems();
      await loadOperations();
      setLoadedAudit(false);
    } catch (reason) {
      setPurgeError(reason instanceof Error ? reason.message : 'Unable to permanently delete item');
    } finally {
      setBusy(false);
    }
  };

  const blockingPurgeItemNumbers = purgeDialog
    ? [
        ...new Set([
          ...(purgeDialog.preview.blocking_item_numbers ?? []),
          ...purgeDialog.preview.external_reference_item_numbers,
        ]),
      ]
    : [];

  // Columns for DataTables
  const protectedReason = (user: User): string | undefined => {
    if (user.username === currentUsername) return 'You cannot remove your own administrator access';
    if (
      user.is_active &&
      user.roles.includes('administrator') &&
      users.filter((candidate) => candidate.is_active && candidate.roles.includes('administrator'))
        .length <= 1
    ) {
      return 'At least one active administrator must remain';
    }
    return undefined;
  };
  const userColumns = useMemo<ColumnDef<User, any>[]>(
    () => [
      {
        id: 'user',
        header: 'User',
        cell: ({ row }) => (
          <div>
            <strong className="table-strong">{row.original.display_name}</strong>
            <small className="muted">{row.original.username}</small>
          </div>
        ),
      },
      {
        id: 'roles',
        header: 'Roles',
        cell: ({ row }) => (
          <div className="admin-user-role-badges">
            {row.original.roles.map((role) => (
              <span key={role}>{role}</span>
            ))}
          </div>
        ),
      },
      {
        id: 'groups',
        header: 'Approval groups',
        cell: ({ row }) => {
          const names = approvalGroups
            .filter((group) => group.member_ids.includes(row.original.id))
            .map((group) => group.name);
          return names.length ? names.join(', ') : '—';
        },
      },
      {
        id: 'status',
        header: 'Status',
        accessorFn: (u) => (!u.is_active ? 'Disabled' : isUserLocked(u) ? 'Locked' : 'Active'),
      },
      {
        id: 'created',
        header: 'Last sign-in',
        accessorFn: (user) =>
          user.last_login_at ? new Date(user.last_login_at).toLocaleString() : 'Never',
      },
      {
        id: 'actions',
        header: '',
        cell: ({ row }) => (
          <button
            type="button"
            onClick={(event) => {
              event.stopPropagation();
              setSelectedUser(row.original);
            }}
          >
            Manage
          </button>
        ),
      },
    ],
    [approvalGroups]
  );

  const filteredUsers = useMemo(() => {
    const query = userSearch.trim().toLocaleLowerCase();
    return users.filter((user) => {
      const matchesSearch =
        !query ||
        user.username.toLocaleLowerCase().includes(query) ||
        user.display_name.toLocaleLowerCase().includes(query);
      const matchesRole = !userRoleFilter || user.roles.includes(userRoleFilter);
      const matchesStatus =
        !userStatusFilter ||
        (userStatusFilter === 'active' && user.is_active && !isUserLocked(user)) ||
        (userStatusFilter === 'locked' && user.is_active && isUserLocked(user)) ||
        (userStatusFilter === 'disabled' && !user.is_active);
      const matchesGroup =
        !userGroupFilter ||
        approvalGroups.some(
          (group) => group.id === userGroupFilter && group.member_ids.includes(user.id)
        );
      return matchesSearch && matchesRole && matchesStatus && matchesGroup;
    });
  }, [approvalGroups, userGroupFilter, userRoleFilter, userSearch, userStatusFilter, users]);

  const itemColumns = useMemo<ColumnDef<Item, any>[]>(
    () =>
      (
        [
          {
            id: 'item_number',
            header: 'Part Number',
            accessorFn: (r) => r.item_number,
            cell: ({ getValue }) => (
              <strong className="table-strong">{getValue() as string}</strong>
            ),
          },
          { id: 'name', header: 'Name', accessorFn: (r) => r.name },
          { id: 'type', header: 'Type', accessorFn: (r) => r.item_type },
          { id: 'cad_system', header: 'CAD System', accessorFn: (r) => r.cad_system ?? '—' },
          {
            id: 'revision',
            header: 'Revision',
            accessorFn: (r) =>
              r.current_revision_code ?? (r.current_revision_id ? '—' : 'Incomplete'),
            cell: ({ getValue }) =>
              ['Incomplete', '—'].includes(String(getValue()))
                ? String(getValue())
                : `Rev ${getValue()}`,
          },
          { id: 'status', header: 'Status', accessorFn: (r) => r.status },
        ] satisfies ColumnDef<Item, any>[]
      ).map((column) => ({ ...column, enableSorting: false })),
    []
  );

  const itemFilterResult = useMemo(() => {
    const errors: string[] = [];
    const compile = (pattern: string, label: string) => {
      if (!pattern) return undefined;
      try {
        return new RegExp(pattern, 'i');
      } catch (reason) {
        errors.push(
          `${label}: ${reason instanceof Error ? reason.message : 'Invalid regular expression'}`
        );
        return undefined;
      }
    };
    compile(itemNumberFilter, 'Part number');
    compile(itemNameFilter, 'Name');
    return {
      errors,
      items,
    };
  }, [itemNameFilter, itemNumberFilter, items]);

  const auditColumns = useMemo<ColumnDef<AuditEntry, any>[]>(
    () => [
      { id: 'action', header: 'Action', accessorFn: (r) => r.action.replaceAll('.', ' ') },
      { id: 'entity', header: 'Entity', accessorFn: (r) => r.entity_type },
      {
        id: 'when',
        header: 'Occurred',
        accessorFn: (r) => new Date(r.occurred_at).toLocaleString(),
      },
    ],
    []
  );

  if (!isAdmin) {
    return (
      <section className="page">
        <p className="error">Administrator access is required.</p>
      </section>
    );
  }

  return (
    <section className="page admin-page">
      <div className="admin-heading">
        <div>
          <h1>Administration</h1>
        </div>
      </div>
      {error && (
        <div className="error" role="alert">
          {error}{' '}
          <button
            type="button"
            onClick={() => {
              if (activeTab === 'operations') void loadOperations();
              else if (activeTab === 'users') void loadUsers();
              else if (activeTab === 'items') void loadItems(itemOffset);
              else if (activeTab === 'numbering') void loadNumbering();
              else void loadAudit();
            }}
          >
            Retry loading
          </button>
        </div>
      )}
      {passwordUser && (
        <div
          className="admin-password-panel"
          role="dialog"
          aria-modal="true"
          aria-label="Reset password"
        >
          <form onSubmit={resetPassword}>
            {error && (
              <p role="alert" className="error">
                {error}
              </p>
            )}
            <strong>Reset password for {passwordUser.username}</strong>
            <input
              name="password"
              type="password"
              minLength={8}
              autoComplete="new-password"
              required
              autoFocus
            />
            <div>
              <button type="submit" disabled={busy}>
                Update password
              </button>
              <button type="button" onClick={() => setPasswordUser(null)}>
                Cancel
              </button>
            </div>
          </form>
        </div>
      )}
      {showCreateUser && (
        <div
          className="admin-password-panel"
          role="dialog"
          aria-modal="true"
          aria-labelledby="create-user-title"
        >
          <form className="admin-user-dialog" onSubmit={createUser}>
            {error && (
              <p role="alert" className="error">
                {error}
              </p>
            )}
            <header>
              <div>
                <strong id="create-user-title">Create user</strong>
                <span>Add an account and assign its initial access.</span>
              </div>
              <button
                type="button"
                aria-label="Close create user"
                onClick={() => setShowCreateUser(false)}
              >
                ×
              </button>
            </header>
            <label>
              Username
              <input name="username" autoComplete="off" required autoFocus />
            </label>
            <label>
              Display name
              <input name="displayName" required />
            </label>
            <fieldset className="admin-user-dialog-options">
              <legend>Roles</legend>
              {roles.map((role) => (
                <label key={role}>
                  <input
                    type="checkbox"
                    name="roles"
                    value={role}
                    defaultChecked={role === 'reader'}
                  />{' '}
                  {role}
                </label>
              ))}
            </fieldset>
            <label>
              Temporary password
              <input
                name="password"
                type="password"
                minLength={8}
                autoComplete="new-password"
                required
              />
            </label>
            <footer>
              <button type="submit" disabled={busy}>
                {busy ? 'Creating…' : 'Create user'}
              </button>
              <button type="button" disabled={busy} onClick={() => setShowCreateUser(false)}>
                Cancel
              </button>
            </footer>
          </form>
        </div>
      )}
      {selectedUser &&
        (() => {
          const protection = protectedReason(selectedUser);
          const groupNames = approvalGroups.filter((group) =>
            group.member_ids.includes(selectedUser.id)
          );
          return (
            <div
              className="admin-password-panel"
              role="dialog"
              aria-modal="true"
              aria-labelledby="manage-user-title"
            >
              <form className="admin-user-dialog" onSubmit={saveUserDetails}>
                {error && (
                  <p role="alert" className="error">
                    {error}
                  </p>
                )}
                <header>
                  <div>
                    <strong id="manage-user-title">{selectedUser.display_name}</strong>
                    <span>
                      @{selectedUser.username} · Created{' '}
                      {new Date(selectedUser.created_at).toLocaleDateString()}
                    </span>
                  </div>
                  <button
                    type="button"
                    aria-label="Close user details"
                    onClick={() => setSelectedUser(null)}
                  >
                    ×
                  </button>
                </header>
                <label>
                  Display name
                  <input
                    name="displayName"
                    defaultValue={selectedUser.display_name}
                    required
                    autoFocus
                  />
                </label>
                <fieldset className="admin-user-dialog-options">
                  <legend>Roles</legend>
                  {roles.map((role) => {
                    const locked =
                      role === 'administrator' && selectedUser.roles.includes(role) && !!protection;
                    return (
                      <label key={role} title={locked ? protection : undefined}>
                        {locked && <input type="hidden" name="roles" value={role} />}
                        <input
                          type="checkbox"
                          name="roles"
                          value={role}
                          defaultChecked={selectedUser.roles.includes(role)}
                          disabled={locked}
                        />{' '}
                        {role}
                      </label>
                    );
                  })}
                </fieldset>
                <fieldset className="admin-user-dialog-options">
                  <legend>Approval groups</legend>
                  {approvalGroups.length > 0 ? (
                    approvalGroups.map((group) => (
                      <label key={group.id}>
                        <input
                          type="checkbox"
                          name="approvalGroups"
                          value={group.id}
                          defaultChecked={group.member_ids.includes(selectedUser.id)}
                        />
                        {group.name}
                        {!group.is_active && ' (inactive)'}
                      </label>
                    ))
                  ) : (
                    <p className="muted">No approval groups are configured.</p>
                  )}
                  <small>Group membership requires an active Reviewer or Administrator role.</small>
                </fieldset>
                <label
                  className="admin-user-active-option"
                  title={selectedUser.is_active ? protection : undefined}
                >
                  {selectedUser.is_active && protection && (
                    <input type="hidden" name="isActive" value="on" />
                  )}
                  <input
                    type="checkbox"
                    name="isActive"
                    defaultChecked={selectedUser.is_active}
                    disabled={selectedUser.is_active && !!protection}
                  />
                  Account enabled
                </label>
                {protection && <p className="maintenance-warning">{protection}</p>}
                <div className="admin-user-dialog-summary">
                  <span>Status</span>
                  <strong>
                    {!selectedUser.is_active
                      ? 'Disabled'
                      : isUserLocked(selectedUser)
                        ? 'Locked'
                        : 'Active'}
                  </strong>
                  <span>Failed sign-ins</span>
                  <strong>{selectedUser.failed_login_attempts}</strong>
                  <span>Locked until</span>
                  <strong>
                    {isUserLocked(selectedUser)
                      ? new Date(selectedUser.locked_until!).toLocaleString()
                      : 'Not locked'}
                  </strong>
                  <span>Approval groups</span>
                  <strong>
                    {groupNames.length ? groupNames.map((group) => group.name).join(', ') : 'None'}
                  </strong>
                  <span>Last sign-in</span>
                  <strong>
                    {selectedUser.last_login_at
                      ? new Date(selectedUser.last_login_at).toLocaleString()
                      : 'Never'}
                  </strong>
                  <span>Password changed</span>
                  <strong>
                    {selectedUser.password_changed_at
                      ? new Date(selectedUser.password_changed_at).toLocaleString()
                      : 'Not recorded'}
                  </strong>
                </div>
                <section className="admin-user-activity" aria-labelledby="user-activity-title">
                  <h3 id="user-activity-title">Recent activity</h3>
                  {userActivityLoading ? (
                    <p className="muted">Loading activity…</p>
                  ) : userActivityError ? (
                    <p role="alert" className="error">
                      {userActivityError}
                    </p>
                  ) : userActivity.length ? (
                    <ul>
                      {userActivity.slice(0, 8).map((activity) => (
                        <li key={activity.id}>
                          <strong>{activity.action.replaceAll('.', ' ')}</strong>
                          <time dateTime={activity.occurred_at}>
                            {new Date(activity.occurred_at).toLocaleString()}
                          </time>
                        </li>
                      ))}
                    </ul>
                  ) : (
                    <p className="muted">No recorded activity.</p>
                  )}
                </section>
                <footer>
                  <button type="submit" disabled={busy}>
                    {busy ? 'Saving…' : 'Save changes'}
                  </button>
                  <button
                    type="button"
                    disabled={busy}
                    onClick={() => {
                      setSelectedUser(null);
                      setPasswordUser(selectedUser);
                    }}
                  >
                    Reset password
                  </button>
                  {isUserLocked(selectedUser) && (
                    <button
                      type="button"
                      disabled={busy}
                      onClick={() => void unlockUser(selectedUser)}
                    >
                      Unlock account
                    </button>
                  )}
                  <button
                    type="button"
                    disabled={busy || selectedUser.username === currentUsername}
                    title={
                      selectedUser.username === currentUsername
                        ? 'You cannot revoke your current session'
                        : undefined
                    }
                    onClick={() => void revokeUserSessions(selectedUser)}
                  >
                    Sign out all sessions
                  </button>
                  <button type="button" disabled={busy} onClick={() => setSelectedUser(null)}>
                    Cancel
                  </button>
                </footer>
              </form>
            </div>
          );
        })()}
      {editingItem && (
        <div
          className="admin-password-panel"
          role="dialog"
          aria-modal="true"
          aria-label="Edit item"
        >
          <form onSubmit={updateItem}>
            {error && (
              <p role="alert" className="error">
                {error}
              </p>
            )}
            <strong>Edit {editingItem.item_number}</strong>
            <label>
              Name
              <input
                name="name"
                defaultValue={editingItem.name}
                maxLength={255}
                required
                autoFocus
              />
            </label>
            <label>
              Category
              <input name="category" defaultValue={editingItem.category ?? ''} maxLength={100} />
            </label>
            <label>
              Description
              <textarea
                name="description"
                defaultValue={editingItem.description ?? ''}
                maxLength={10000}
                rows={4}
              />
            </label>
            <div>
              <button type="submit" disabled={busy}>
                Save changes
              </button>
              <button type="button" onClick={() => setEditingItem(null)}>
                Cancel
              </button>
            </div>
          </form>
        </div>
      )}
      {revisionDeletionDialog &&
        (() => {
          const selectedRevision = revisionDeletionDialog.revisions.find(
            (revision) => revision.id === revisionDeletionDialog.selectedRevisionId
          );
          return (
            <div
              className="admin-password-panel"
              role="dialog"
              aria-modal="true"
              aria-labelledby="delete-revision-title"
            >
              <form className="admin-purge-form" onSubmit={confirmRevisionDeletion}>
                <strong id="delete-revision-title">
                  Delete a revision from {revisionDeletionDialog.item.item_number}
                </strong>
                <p>
                  Select one mutable revision. Released, superseded, obsolete, checked-out, and
                  referenced revisions are protected.
                </p>
                <fieldset className="admin-revision-deletion-list">
                  <legend>Revisions</legend>
                  {revisionDeletionDialog.revisions.length === 0 && (
                    <p>This Item has no revisions.</p>
                  )}
                  {revisionDeletionDialog.revisions.map((revision) => (
                    <label key={revision.id} title={revision.reason ?? undefined}>
                      <input
                        type="radio"
                        name="revision"
                        value={revision.id}
                        checked={revisionDeletionDialog.selectedRevisionId === revision.id}
                        disabled={busy || !revision.eligible}
                        onChange={() =>
                          setRevisionDeletionDialog((current) =>
                            current
                              ? {
                                  ...current,
                                  selectedRevisionId: revision.id,
                                  confirmation: '',
                                }
                              : null
                          )
                        }
                      />
                      <span>
                        <strong>Revision {revision.revision_code}</strong>
                        {revision.is_current && ' · Current'} · {revision.lifecycle_state}
                        <small>
                          {revision.reason ??
                            `${revision.state} · Created ${new Date(revision.created_at).toLocaleString()}`}
                        </small>
                      </span>
                    </label>
                  ))}
                </fieldset>
                {selectedRevision?.eligible && (
                  <label>
                    Type <strong>{selectedRevision.revision_code}</strong> to confirm permanent
                    deletion
                    <input
                      value={revisionDeletionDialog.confirmation}
                      onChange={(event) =>
                        setRevisionDeletionDialog((current) =>
                          current
                            ? {
                                ...current,
                                confirmation: event.target.value,
                              }
                            : null
                        )
                      }
                      autoFocus
                      required
                    />
                  </label>
                )}
                {revisionDeletionError && (
                  <p className="error admin-purge-error" role="alert">
                    {revisionDeletionError}
                  </p>
                )}
                <div>
                  <button
                    className="danger-button"
                    type="submit"
                    disabled={
                      busy ||
                      !selectedRevision?.eligible ||
                      revisionDeletionDialog.confirmation !== selectedRevision.revision_code
                    }
                  >
                    {busy ? 'Deleting…' : 'Delete revision'}
                  </button>
                  <button
                    type="button"
                    disabled={busy}
                    onClick={() => {
                      setRevisionDeletionDialog(null);
                      setRevisionDeletionError('');
                    }}
                  >
                    Cancel
                  </button>
                </div>
              </form>
            </div>
          );
        })()}
      {purgeDialog && (
        <div
          className="admin-password-panel"
          role="dialog"
          aria-modal="true"
          aria-label="Confirm permanent deletion"
        >
          <form className="admin-purge-form" onSubmit={confirmPurge}>
            <strong>Permanently delete {purgeDialog.item.item_number}?</strong>
            <p>
              This removes {purgeDialog.preview.revision_count} revision(s),{' '}
              {purgeDialog.preview.artifact_count} artifact(s),{' '}
              {purgeDialog.preview.attachment_count} attachment(s), and{' '}
              {formatBytes(purgeDialog.preview.stored_bytes)} of assembly files.
            </p>
            {purgeDialog.preview.cascade_item_numbers.length > 0 && (
              <fieldset>
                <legend>Referenced Items eligible for deletion</legend>
                <p>
                  These Items have no relationships outside this assembly. They are kept unless you
                  explicitly select them.
                </p>
                <div className="admin-purge-selection-actions">
                  <button
                    type="button"
                    disabled={
                      busy ||
                      purgeDialog.selectedItemNumbers.length ===
                        purgeDialog.preview.cascade_item_numbers.length
                    }
                    onClick={() =>
                      setPurgeDialog((current) =>
                        current
                          ? {
                              ...current,
                              selectedItemNumbers: [...current.preview.cascade_item_numbers],
                            }
                          : null
                      )
                    }
                  >
                    Select all
                  </button>
                  <button
                    type="button"
                    disabled={busy || purgeDialog.selectedItemNumbers.length === 0}
                    onClick={() =>
                      setPurgeDialog((current) =>
                        current
                          ? {
                              ...current,
                              selectedItemNumbers: [],
                            }
                          : null
                      )
                    }
                  >
                    Deselect all
                  </button>
                </div>
                <div
                  className="admin-purge-reference-list"
                  tabIndex={0}
                  aria-label="Referenced Items eligible for deletion"
                >
                  {purgeDialog.preview.cascade_item_numbers.map((itemNumber) => (
                    <label key={itemNumber}>
                      <input
                        type="checkbox"
                        checked={purgeDialog.selectedItemNumbers.includes(itemNumber)}
                        onChange={(event) =>
                          setPurgeDialog((current) =>
                            current
                              ? {
                                  ...current,
                                  selectedItemNumbers: event.target.checked
                                    ? [...current.selectedItemNumbers, itemNumber]
                                    : current.selectedItemNumbers.filter(
                                        (value) => value !== itemNumber
                                      ),
                                }
                              : null
                          )
                        }
                      />
                      Delete {itemNumber}
                    </label>
                  ))}
                </div>
              </fieldset>
            )}
            {purgeDialog.preview.protected_legacy_item_numbers.length > 0 && (
              <p className="maintenance-warning">
                <strong>Kept components:</strong> These referenced Items have other relationships
                and cannot be selected for deletion:{' '}
                {purgeDialog.preview.protected_legacy_item_numbers.join(', ')}.
              </p>
            )}
            {purgeDialog.preview.blocking_reference_count > 0 && (
              <div className="error admin-purge-blockers" role="alert">
                <strong>Deletion is currently blocked</strong>
                <p>These external Items still reference this Item or its files:</p>
                <ul>
                  {blockingPurgeItemNumbers.map((itemNumber) => (
                    <li key={itemNumber}>{itemNumber}</li>
                  ))}
                </ul>
                <p>Remove those relationships first, or archive this Item instead.</p>
              </div>
            )}
            {purgeError && (
              <p className="error admin-purge-error" role="alert">
                {purgeError}
              </p>
            )}
            <label>
              Type <strong>{purgeDialog.item.item_number}</strong> to confirm
              <input
                value={purgeDialog.confirmation}
                onChange={(event) =>
                  setPurgeDialog((current) =>
                    current ? { ...current, confirmation: event.target.value } : null
                  )
                }
                autoFocus={purgeDialog.preview.blocking_reference_count === 0}
                disabled={purgeDialog.preview.blocking_reference_count > 0}
                required
              />
            </label>
            <div>
              <button
                className="danger-button"
                type="submit"
                disabled={
                  busy ||
                  purgeDialog.preview.blocking_reference_count > 0 ||
                  purgeDialog.confirmation !== purgeDialog.item.item_number
                }
              >
                Permanently delete
              </button>
              <button
                type="button"
                disabled={busy}
                onClick={() => {
                  setPurgeDialog(null);
                  setPurgeError('');
                }}
              >
                Cancel
              </button>
            </div>
          </form>
        </div>
      )}

      {showAddNumberTemplate && (
        <div
          className="admin-password-panel"
          role="dialog"
          aria-modal="true"
          aria-label="Add Item-number template"
        >
          <form onSubmit={createNumberTemplate}>
            {error && (
              <p role="alert" className="error">
                {error}
              </p>
            )}
            <strong>Add Item-number template</strong>
            <label>
              Name
              <input name="name" autoFocus required />
            </label>
            <label>
              Part pattern
              <input name="partPattern" defaultValue="PART-{sequence:06d}" required />
            </label>
            <label>
              Part starting sequence
              <input name="partNextValue" type="number" min="1" defaultValue="1" required />
            </label>
            <label>
              Assembly pattern
              <input name="assemblyPattern" defaultValue="ASM-{sequence:06d}" required />
            </label>
            <label>
              Assembly starting sequence
              <input name="assemblyNextValue" type="number" min="1" defaultValue="1" required />
            </label>
            <label>
              <input name="selected" type="checkbox" /> Select as default
            </label>
            <div>
              <button disabled={busy}>Add template</button>
              <button type="button" disabled={busy} onClick={() => setShowAddNumberTemplate(false)}>
                Cancel
              </button>
            </div>
          </form>
        </div>
      )}

      <div className="admin-tabs">
        <div className="admin-tab-list" aria-label="Administration sections">
          <div style={{ display: 'flex', flexWrap: 'wrap', gap: '0.5rem' }}>
            <button
              className={activeTab === 'operations' ? 'admin-tab active' : 'admin-tab'}
              aria-pressed={activeTab === 'operations'}
              onClick={() => {
                selectTab('operations');
              }}
            >
              Operations
            </button>

            <button
              className={activeTab === 'users' ? 'admin-tab active' : 'admin-tab'}
              aria-pressed={activeTab === 'users'}
              onClick={() => {
                selectTab('users');
              }}
            >
              User management
            </button>

            <button
              className={activeTab === 'items' ? 'admin-tab active' : 'admin-tab'}
              aria-pressed={activeTab === 'items'}
              onClick={() => {
                selectTab('items');
              }}
            >
              Item management
            </button>

            <button
              className={activeTab === 'numbering' ? 'admin-tab active' : 'admin-tab'}
              aria-pressed={activeTab === 'numbering'}
              onClick={() => selectTab('numbering')}
            >
              Item numbering
            </button>

            <button
              className={activeTab === 'audit' ? 'admin-tab active' : 'admin-tab'}
              aria-pressed={activeTab === 'audit'}
              onClick={() => {
                selectTab('audit');
              }}
            >
              Recent audit activity
            </button>
          </div>
        </div>

        <div className="admin-tab-panels">
          {activeTab === 'operations' && (
            <OperationsPanel
              status={status}
              cadWorkerStatus={cadWorkerStatus}
              cadWorkerSettings={cadWorkerSettings}
              setCadWorkerSettings={setCadWorkerSettings}
              setCadWorkerSettingsSaved={setCadWorkerSettingsSaved}
              cadWorkerSettingsBusy={cadWorkerSettingsBusy}
              cadWorkerStatusBusy={cadWorkerStatusBusy}
              cadWorkerSettingsSaved={cadWorkerSettingsSaved}
              loading={loadingOps}
              notificationBusy={notificationBusy}
              notificationRun={notificationRun}
              onSaveSettings={saveCadWorkerSettings}
              onRefresh={checkCadWorkerStatus}
              onRunNotifications={runCheckoutNotifications}
            />
          )}

          {activeTab === 'users' && (
            <UsersPanel
              users={users}
              filteredUsers={filteredUsers}
              approvalGroups={approvalGroups}
              columns={userColumns}
              loading={loadingUsers}
              busy={busy}
              search={userSearch}
              roleFilter={userRoleFilter}
              statusFilter={userStatusFilter}
              groupFilter={userGroupFilter}
              setSearch={setUserSearch}
              setRoleFilter={setUserRoleFilter}
              setStatusFilter={setUserStatusFilter}
              setGroupFilter={setUserGroupFilter}
              setSelectedUser={setSelectedUser}
              setShowCreateUser={setShowCreateUser}
              onRefresh={loadUsers}
              onCreateGroup={createApprovalGroup}
              onToggleGroup={toggleApprovalGroup}
            />
          )}

          {activeTab === 'items' && (
            <ItemsPanel
              filteredItems={itemFilterResult.items}
              errors={itemFilterResult.errors}
              columns={itemColumns}
              loading={loadingItems}
              numberFilter={itemNumberFilter}
              nameFilter={itemNameFilter}
              total={itemTotal}
              offset={itemOffset}
              pageSize={25}
              setNumberFilter={setItemNumberFilter}
              setNameFilter={setItemNameFilter}
              filters={itemFilters}
              facets={itemFacets}
              headerFiltersAvailable={itemHeaderFiltersAvailable}
              onFiltersChange={changeItemFilters}
              onClear={clearItemFilters}
              onApply={applyItemSearch}
              onPageChange={loadItems}
              onRefresh={() => loadItems(itemOffset)}
              onEdit={setEditingItem}
              onDeleteRevision={deleteRevision}
              onArchive={deleteItem}
              onRestore={toggleItem}
              onPurge={purgeItem}
            />
          )}

          {activeTab === 'numbering' && (
            <NumberingPanel
              templates={numberTemplates}
              loading={loadingNumbering}
              setShowAdd={setShowAddNumberTemplate}
              onRefresh={loadNumbering}
              onEdit={editNumberTemplate}
              onUpdate={updateNumberTemplate}
            />
          )}

          {activeTab === 'audit' && (
            <AuditPanel
              entries={audit}
              columns={auditColumns}
              loading={loadingAudit}
              onRefresh={loadAudit}
            />
          )}
        </div>
      </div>
    </section>
  );
}
