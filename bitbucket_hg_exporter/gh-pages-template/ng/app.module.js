'use strict';

// Define the `phonecatApp` module
var app = angular.module('BitbucketBackupApp', [
  'ui.bootstrap',
  'ngRoute',
  'issuesList',
  'issueDetails',
  'indexPage',
  'sidebarLinks',
  'repoList',
  'pullrequestsList',
  'pullrequestDetails',
  'commitDetails'
]);

app.run(['$rootScope', '$location', '$anchorScroll', '$routeParams', '$http', '$timeout', 'InitService',
function($rootScope, $location, $anchorScroll, $routeParams, $http, $timeout, Init) {
  // $rootScope.project_name = 'empty';
  // $rootScope.relative_project_url = 'data/repositories/philipstarkey/qtutils/';
  // $http.get($rootScope.relative_project_url + '../qtutils.json').then(function(response) {
  //   $rootScope.project_name = response.data['name'];
  //   $rootScope.project_data = response.data;

  //   $rootScope.links = [
  //     {text: 'Home', url:'#!/'},
  //     {text: 'Issues', url:'#!/issues'},
  //   ];
  // });

  $rootScope.projects = {};
  $rootScope.project_data = {};
  $http.get('repos.json').then(function(response){
    $rootScope.projects = response.data;
    var promises = [];
    angular.forEach($rootScope.projects,  function(value, key){
      promises.push($http.get(value['project_file']).then(function(p_response) {
        $rootScope.project_data[key] = p_response.data;
      }));
    });
    Promise.all(promises).then(function(){Init.defer.resolve();});
  });
  
  $rootScope.$on("$routeChangeStart", function (event, next, current) {
    if (next.params.hasOwnProperty('pageId')) {
      next.params.pageId = Number(next.params.pageId);
    }
  });
  $rootScope.$on('$routeChangeSuccess', function(newRoute, oldRoute) {
    $timeout(function(){$anchorScroll($location.hash());}, 200);  
  });

}])

app.service('InitService', ['$q', function ($q) {
  var d = $q.defer();
  return {
    defer: d,
    promise: d.promise 
  };
}]);