import { Component, EventEmitter, Input, Output, signal } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { MatButtonModule } from '@angular/material/button';
import { MatCardModule } from '@angular/material/card';
import { MatCheckboxModule } from '@angular/material/checkbox';
import { MatFormFieldModule } from '@angular/material/form-field';
import { MatInputModule } from '@angular/material/input';
import { MatProgressBarModule } from '@angular/material/progress-bar';
import { MatSelectModule } from '@angular/material/select';
import { ApiService } from './api.service';
import { Host, NetworkProfile, ProbeResult } from './models';

@Component({
  selector: 'app-network',
  styles: [`.probe-heading { display: grid; gap: .5rem; } .probe-heading > * { min-width: 0; }`],
  imports: [
    FormsModule,
    MatButtonModule,
    MatCardModule,
    MatCheckboxModule,
    MatFormFieldModule,
    MatInputModule,
    MatProgressBarModule,
    MatSelectModule,
  ],
  template: `
    <header class="page-head">
      <div>
        <p class="eyebrow">NETWORK ASSIST</p>
        <h1>网络借助</h1>
        <p>只探测已配置 SSH 的主机；启用前检查出口，切换后复检客户端，失败自动回退。</p>
      </div>
      <button mat-flat-button (click)="probeAll()" [disabled]="busy()">探测全部主机</button>
    </header>
    @if (busy()) {
      <mat-progress-bar mode="indeterminate" />
    }
    @if (message()) {
      <div class="alert" [class.error]="failed()">{{ message() }}</div>
    }
    <section class="panel-section">
      <div class="section-title">
        <div>
          <h2>连通性检查</h2>
          <p>支持 Windows 与 Ubuntu。分别检查 SSH、DNS 和直连公网；Windows 额外检查系统代理。出口机使用 Ubuntu/Linux。</p>
        </div>
      </div>
      <div class="probe-grid">
        @for (host of hosts; track host.id) {
          <mat-card class="probe-card"
            ><div class="probe-heading">
              <strong class="long-value">{{ host.name }}</strong
              ><span class="mono long-value">{{ host.address }}</span>
            </div>
            <div class="probe-status">
              <span [class.ok]="result(host.id)?.ssh">SSH</span
              ><span [class.ok]="result(host.id)?.dns">DNS</span
              ><span [class.ok]="result(host.id)?.internet">直连公网</span
              ><span [class.ok]="result(host.id)?.helper">辅助程序</span>
              @if (result(host.id)?.os === 'Windows') {
                <span [class.ok]="result(host.id)?.system_internet">系统应用联网</span>
              }
            </div>
            <p class="long-value">{{ result(host.id)?.os }}</p>
            @if (result(host.id)?.diagnosis === 'system_proxy_failed') {
              <p class="alert error long-value">直连正常，但系统代理请求失败。请检查 Windows 设置中的代理或代理客户端，浏览器可能因此无法联网。</p>
            }
            @if (result(host.id)?.diagnosis === 'proxy_only') {
              <p class="long-value">仅系统代理可联网，直连出口不可用。</p>
            }
            <p class="long-value">
              {{ result(host.id)?.error || result(host.id)?.default_route || '尚未探测' }}
            </p>
          </mat-card>
        } @empty {
          <div class="empty-state">请先在“主机与凭据”页添加主机。</div>
        }
      </div>
    </section>
    <div class="split-layout">
      <section class="panel-section">
        <div class="section-title">
          <h2>借网方案</h2>
          <button mat-button (click)="newProfile()">新建</button>
        </div>
        <div class="profile-list">
          @for (profile of profiles; track profile.id) {
            <button
              class="profile-item"
              [class.selected]="draft.id === profile.id"
              (click)="edit(profile)"
            >
              <span
                ><strong class="long-value">{{ profile.name }}</strong
                ><small
                  >{{ hostName(profile.gateway_id) }} →
                  {{ profile.client_ids.length }} 台客户端</small
                ></span
              ><span
                class="state-badge"
                [class.enabled]="profile.state === 'enabled'"
                [class.error]="profile.state === 'error'"
                >{{ stateText(profile.state) }}</span
              >
            </button>
          } @empty {
            <div class="empty-state">还没有借网方案。</div>
          }
        </div>
      </section>
      <mat-card class="editor-card">
        <h2>{{ draft.id ? '方案设置' : '新建借网方案' }}</h2>
        <div class="form-grid">
          <mat-form-field appearance="outline" class="wide"
            ><mat-label>方案名称</mat-label><input matInput [(ngModel)]="draft.name"
          /></mat-form-field>
          <mat-form-field appearance="outline"
            ><mat-label>出口机</mat-label
            ><mat-select [(ngModel)]="draft.gateway_id">
              @for (host of hosts; track host.id) {
                <mat-option [value]="host.id">{{ host.name }}</mat-option>
              }
            </mat-select></mat-form-field
          >
          <mat-form-field appearance="outline"
            ><mat-label>客户端</mat-label
            ><mat-select multiple [(ngModel)]="draft.client_ids">
              @for (host of clientChoices; track host.id) {
                <mat-option [value]="host.id">{{ host.name }}</mat-option>
              }
            </mat-select></mat-form-field
          >
          <mat-form-field appearance="outline"
            ><mat-label>出口 UDP 端口</mat-label
            ><input matInput type="number" [(ngModel)]="draft.port"
          /></mat-form-field>
          <mat-form-field appearance="outline"
            ><mat-label>隧道网段</mat-label
            ><input matInput [(ngModel)]="draft.tunnel_cidr" placeholder="自动分配"
          /></mat-form-field>
          <mat-form-field appearance="outline" class="wide"
            ><mat-label>客户端可达的出口地址</mat-label
            ><input matInput [(ngModel)]="draft.endpoint" placeholder="默认使用出口机地址"
          /></mat-form-field>
          <mat-form-field appearance="outline" class="wide"
            ><mat-label>切换后仍走原网络的 CIDR（空格或逗号分隔）</mat-label
            ><textarea matInput rows="3" [(ngModel)]="preserveText"></textarea>
          </mat-form-field>
        </div>
        <mat-checkbox [(ngModel)]="draft.maintenance"
          >每分钟维护隧道，连续失败后自动断开并恢复原路由</mat-checkbox
        >
        @if (draft.last_error) {
          <div class="alert error long-value">{{ draft.last_error }}</div>
        }
        <div class="button-row">
          <button mat-flat-button (click)="save()" [disabled]="active">保存</button
          ><button
            mat-stroked-button
            (click)="installHelpers()"
            [disabled]="!draft.gateway_id || !draft.client_ids.length"
          >
            安装辅助程序
          </button>
          @if (draft.state === 'enabled') {
            <button mat-flat-button class="danger-bg" (click)="disable()">断开并恢复原网络</button>
          } @else {
            <button mat-flat-button (click)="enable()" [disabled]="!draft.id">启用借网</button>
          }
          <button mat-button class="danger" (click)="remove()" [disabled]="!draft.id || active">
            删除
          </button>
        </div>
      </mat-card>
    </div>
  `,
})
export class NetworkComponent {
  @Input({ required: true }) hosts: Host[] = [];
  @Input({ required: true }) profiles: NetworkProfile[] = [];
  @Input({ required: true }) probes: ProbeResult[] = [];
  @Output() changed = new EventEmitter<void>();
  @Output() probesChanged = new EventEmitter<ProbeResult[]>();
  readonly busy = signal(false);
  readonly message = signal('');
  readonly failed = signal(false);
  draft = this.emptyProfile();
  preserveText = '';
  constructor(private readonly api: ApiService) {}
  get clientChoices() {
    return this.hosts.filter((value) => value.id !== this.draft.gateway_id);
  }
  get active() {
    return !['disabled', 'error'].includes(this.draft.state);
  }
  emptyProfile(): NetworkProfile {
    return {
      id: '',
      name: '',
      gateway_id: '',
      client_ids: [],
      port: 51919,
      endpoint: '',
      tunnel_cidr: '',
      preserve_routes: [],
      maintenance: true,
      state: 'disabled',
      updated_at: 0,
      interface: '',
      last_error: '',
    };
  }
  result(id: string) {
    return this.probes.find((value) => value.id === id);
  }
  hostName(id: string) {
    return this.hosts.find((value) => value.id === id)?.name || id;
  }
  stateText(state: string) {
    return (
      (
        {
          disabled: '未启用',
          enabling: '启用中',
          enabled: '借网中',
          disabling: '断开中',
          error: '异常',
        } as Record<string, string>
      )[state] || state
    );
  }
  notify(message: string, failed = false) {
    this.message.set(message);
    this.failed.set(failed);
  }
  newProfile() {
    this.draft = this.emptyProfile();
    this.preserveText = '';
  }
  edit(profile: NetworkProfile) {
    this.draft = {
      ...profile,
      client_ids: [...profile.client_ids],
      preserve_routes: [...profile.preserve_routes],
    };
    this.preserveText = profile.preserve_routes.join('\n');
  }
  probeAll() {
    this.busy.set(true);
    this.api
      .post<{ results: ProbeResult[] }>('/api/network/probe', { ids: this.hosts.map((v) => v.id) })
      .subscribe({
        next: (value) => {
          this.busy.set(false);
          this.probesChanged.emit(value.results);
          this.notify('探测完成');
        },
        error: (e) => {
          this.busy.set(false);
          this.notify(e.message, true);
        },
      });
  }
  save() {
    const payload = { ...this.draft, preserve_routes: this.preserveText };
    this.api.post<{ profile: NetworkProfile }>('/api/network/profile/save', payload).subscribe({
      next: (value) => {
        this.edit(value.profile);
        this.notify('方案已保存');
        this.changed.emit();
      },
      error: (e) => this.notify(e.message, true),
    });
  }
  reauthThen(action: () => void) {
    const password = prompt('此操作会修改远端网络，请输入管理员密码（5 分钟内无需再次输入）：');
    if (password === null) return;
    this.api
      .post('/api/reauth', { password })
      .subscribe({ next: action, error: (e) => this.notify(e.message, true) });
  }
  installHelpers() {
    this.reauthThen(() => {
      this.busy.set(true);
      const ids = [this.draft.gateway_id, ...this.draft.client_ids];
      this.api.post('/api/network/helper/install', { ids }).subscribe({
        next: () => {
          this.busy.set(false);
          this.notify('辅助程序已安装，请重新探测');
        },
        error: (e) => {
          this.busy.set(false);
          this.notify(e.message, true);
        },
      });
    });
  }
  enable() {
    if (
      !confirm(
        '启用后，所选客户端的默认公网流量将经出口机转发。系统会保留 SSH 控制路由并在复检失败时回退。继续吗？',
      )
    )
      return;
    this.reauthThen(() => this.changeState('/api/network/profile/enable', '借网已启用'));
  }
  disable() {
    if (
      !confirm('断开后将删除借网隧道与临时路由，恢复客户端原有校园网、热点或本地默认路由。继续吗？')
    )
      return;
    this.reauthThen(() => this.changeState('/api/network/profile/disable', '已断开并恢复原网络'));
  }
  changeState(path: string, success: string) {
    this.busy.set(true);
    this.api.post<{ profile: NetworkProfile }>(path, { id: this.draft.id }).subscribe({
      next: (value) => {
        this.busy.set(false);
        this.edit(value.profile);
        this.notify(success);
        this.changed.emit();
      },
      error: (e) => {
        this.busy.set(false);
        this.notify(e.message, true);
        this.changed.emit();
      },
    });
  }
  remove() {
    if (!confirm(`确定删除方案“${this.draft.name}”吗？`)) return;
    this.reauthThen(() =>
      this.api.post('/api/network/profile/delete', { id: this.draft.id }).subscribe({
        next: () => {
          this.newProfile();
          this.changed.emit();
        },
        error: (e) => this.notify(e.message, true),
      }),
    );
  }
}
