import promisify from './libs/api-promise';
/** 以上引入了公共库，作用是：
 * promisify: 提供 Promise 化的 API，通过 tt.promises.API_NAME 使用
 */

function init() {
  promisify();
}

Block({
  data: {
    info: '',
    height: 0,
  },
  onLoad(options) {
    // Block 加载完成
    init();
    // 通过 options 可以拿到宿主信息和 BlockInfo
    console.log(options.host, options.blockInfo);

    // 关闭 Block 的 Loading，当 useStartLoading=true 时有效
    tt.hideBlockLoading();
    // 监听 Block 的容器尺寸
    tt.onContainerResize((size) => {
      this.setData({
        height: size.height
      })
    })
  },
  async onReady() {
    // Block 首次渲染完成
    const rect = await tt.promises.getContainerRect();

    this.setData({
      height: rect.height,
    });
  },
  onShow() {
    // Block 显示
  },
  onHide() {
    // Block 隐藏
  },
  onDestroy() {
    // Block 被销毁
  },
  onActivate(state) {
    // 激活的生命周期
    // 激活时可以获取到上次失活保存的数据
    init();
    this.setData(state);
  },
  onInactivate() {
    // 失活的生命周期
    // 失活时这里将 data 数据临时保存起来
    tt.saveState({
      state: this.data,
    });
  },
  onResourceChange(sourceData) {
    // 数据协同接收到的数据
    console.log(sourceData);
  },
  methods: {
    async request() {
      const response = await tt.promises.request({
        url: 'https://www.feishu.cn/api/downloads',
        method: 'GET',
      });

      this.setData({
        info: JSON.stringify(response.data, null, 2),
      });
    },
    async getUserInfo() {
      const { code } = await tt.promises.login();
      const { userInfo } = await tt.promises.getUserInfo();

      this.setData({
        info: JSON.stringify({
          code,
          userInfo,
        }, null, 2),
      });
    },
    async getSystemInfo() {
      const systemInfo = await tt.promises.getSystemInfo();

      this.setData({
        info: JSON.stringify(systemInfo, null, 2),
      });
    },
    async getContainerRect() {
      const rect = await tt.promises.getContainerRect()
      this.setData({
        info: JSON.stringify(rect, null, 2)
      })

    },
    showToast() {
      tt.showToast({
        title: 'show toast success',
        icon: 'success',
        duration: 1500,
        success(res) {
          console.log('showToast 调用成功', res.errMsg);
        },
        fail(res) {
          console.log('showToast 调用失败', res.errMsg);
        },
        complete(res) {
          console.log('showToast 调用结束', res.errMsg);
        }
      });
    }
  },
});
