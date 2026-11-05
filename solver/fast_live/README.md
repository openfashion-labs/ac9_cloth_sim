# AC9 Fast Live の計算境界

Live Simで明示選択する実験モード。通常Nativeの既定は変えない。
静止したmesh体1個と独立したBW布頂点・縫合ばねが対象。自己接触、曲げヒステリシス、Layer、従属頂点、動く体、貫通つかみは対応外。

各コマ2分割、Native20反復の最後に全体補正1回。GPUで9成分の膜接線を射影し、蝶番GN・ばね・体接触・固定した摩擦/法線減衰を合わせ、ブロックJacobi PCGで解く。tol1e-7、atol1e-12、上限3000、10反復ごとの判定。再帰残差と真の残差の両方を確認する。

Fastでは研究で検証した体側kd=0、粒子側kd=.01を使い、Nativeが合成する接触減衰は.005。通常Nativeは従来の体側既定kd=100を維持するため合成50.005であり、両モードの減衰が同じという意味ではない。摩擦係数と減衰は各prepared modelの実配列から読み、contractとの一致も確認する。

補正の受理は増分エネルギーと体の表裏/移動経路ガード。補正が確定するのは位置だけで、Nativeの速度更新→摩擦アンカー更新→state swapの前に置く。補正中のNative履歴は読取り専用。ガード違反、線形破綻、例外は最後の完了コマを残して停止し、不完全な分割をcheckpointへ保存しない。

質量/逆質量/flags/膜の基準・材料/蝶番係数は各分割で同期する。自動縫合ramp/delay・縫合on/offを無効にしない。重力とspring grabは、その分割の最新外力を含むNative inertiaを使う。折り/elasticの実行中変更と貫通grabは適用前に拒否する。spring grabの外力は既存Nativeと同じ陽的評価であり、無抵抗の全点つかみは高周波で振動し得る（精度/操作性の保証ではない）。

private GPU copies・graph・energy cacheはcheckpoint復元後に再生成する。方式/version/実効刻み/反復/線形精度/摩擦/減衰/graph/対象範囲をresume gateとsource/config fingerprintで固定する。方式・入力が変われば表示形から再構築する。

由来: 2026-11-01 Codex S3 body-contactと2026-11-02 Claude fast-frictionの検証済み候補。研究用の絶対パス、診断probe、sys.setprofile、CPU解法、AMGは持ち込まない。各moduleの計算はこの隔離パッケージ内で完結し、既定Nativeの共有kernelは変更しない。
