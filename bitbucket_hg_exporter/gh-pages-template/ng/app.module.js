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
]);

app.run(function($rootScope, $location, $anchorScroll, $routeParams, $http, $timeout) {
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
    angular.forEach($rootScope.projects,  function(value, key){
      $http.get(value['project_file']).then(function(p_response) {
        $rootScope.project_data[key] = p_response.data;
      });
    });
  });

  $rootScope.$on('$routeChangeSuccess', function(newRoute, oldRoute) {
    $timeout(function(){$anchorScroll($location.hash());}, 200);  
  });

})